"""Celery task: receive -> render -> send -> ack.

Runs in its own worker process (celery -A app.worker.celery_app:celery_app
worker), sharing the same models/services as the API. Redis/Celery replaced
the ElasticMQ-emulated-SQS setup this used to poll (see git history of
consumer.py); the business logic below is unchanged from that version --
only how a message arrives changed, from an SQS receive-loop to a Celery
task invocation.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.core.blob_client import BlobClient, get_blob_client
from app.core.decorators import log_call
from app.core.email_provider import (
    EmailProvider,
    EmailSendError,
    get_email_provider_for_project,
)
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.db.session import engine, session_factory
from app.messages.models import Message, MessageStatus
from app.messages.repository import MessageRepository
from app.messages.schemas import MessageCreate
from app.messages.service import MessageService
from app.projects.repository import ProjectRepository
from app.projects.service import ProjectService
from app.rejected_messages.repository import RejectedMessageRepository
from app.rejected_messages.schemas import RejectedMessageCreate
from app.rejected_messages.service import RejectedMessageService
from app.settings.config import settings
from app.templates_.repository import TemplateRepository
from app.templates_.service import TemplateService
from app.worker.celery_app import TASK_NAME, celery_app

logger = logging.getLogger(__name__)

MAX_RETRIES = 5


class MalformedMessageError(Exception):
    def __init__(self, detail: str):
        self.detail = detail


def _parse_task_args(
    project_id: str, template_key: str, to_email: str, payload: dict[str, Any] | None
) -> tuple[uuid.UUID, str, str, dict[str, Any]]:
    try:
        parsed_project_id = uuid.UUID(project_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise MalformedMessageError(f"Malformed relay message: {exc}") from exc
    if not template_key or not to_email:
        raise MalformedMessageError("Malformed relay message: template_key and to are required")
    return parsed_project_id, template_key, to_email, dict(payload or {})


@log_call
async def process_message(
    broker_message_id: str,
    project_id: str,
    template_key: str,
    to_email: str,
    payload: dict[str, Any] | None,
    email_provider: EmailProvider | None = None,
    blob_client: BlobClient | None = None,
    final_attempt: bool = False,
) -> None:
    """`email_provider` is an override (tests); normally it is resolved from the
    project's own `delivery_provider`/`delivery_config` once the project is loaded.
    `final_attempt` is True on the last Celery try: a transient failure then becomes
    terminal (no further automatic attempt will happen).
    """
    blob = blob_client or get_blob_client()
    async with session_factory() as session:
        project_service = ProjectService(ProjectRepository(session))
        template_service = TemplateService(TemplateRepository(session))
        message_service = MessageService(MessageRepository(session))

        parsed_project_id, template_key, to_email, render_payload = _parse_task_args(
            project_id, template_key, to_email, payload
        )

        existing = await message_service.get_by_broker_message_id(
            broker_message_id, parsed_project_id
        )
        if existing is not None and existing.status == MessageStatus.SENT:
            return
        if existing is not None and existing.terminal_failure:
            # A redelivery of a message that already failed for good (permanent provider
            # error, blocked recipient, retries spent) must not send it again; an admin
            # can still resend it explicitly.
            logger.warning("ignoring redelivery of terminally failed message %s", existing.id)
            return

        message: Message | None = existing
        try:
            project = await project_service.get(parsed_project_id)
            if not project.is_active:
                logger.warning("skipping message for inactive project %s", parsed_project_id)
                return

            template = await template_service.get_active(parsed_project_id, template_key)

            # A Celery retry reuses the task id: keep one row per logical message
            # (attempts counts the tries) instead of inserting a duplicate per
            # attempt (acidevbutter/butter_relay#35). The row is created before the
            # optional Blob lookup so a Blob failure is recorded too (DVB-344).
            message = existing or await message_service.create(
                MessageCreate(
                    project_id=parsed_project_id,
                    template_id=template.id,
                    to_email=to_email,
                    payload=render_payload,
                    broker_message_id=broker_message_id,
                    provider=project.delivery_provider,
                )
            )

            blob_id = render_payload.get("blob_id")
            if blob_id:
                blob_meta = await blob.get(str(blob_id))
                public_url = blob_meta.get("public_url")
                if not isinstance(public_url, str) or not public_url:
                    raise ValidationFailedError("NFS-e blob has no public_url")
                render_payload["cta_link"] = public_url

            subject, html = template_service.render(template, render_payload)
            # Store what was actually rendered (cta_link included) for the message log.
            message = await message_service.mark_processing(message, subject, render_payload)

            if not settings.is_recipient_allowed(to_email):
                detail = "recipient blocked by non-production delivery policy"
                logger.warning("%s: %s", detail, to_email)
                await message_service.mark_failed(message, detail, terminal=True)
                return

            provider = email_provider or get_email_provider_for_project(
                project.delivery_provider, project.delivery_config, settings
            )
            result = await provider.send(
                to=to_email,
                subject=subject,
                html=html,
                from_email=project.default_sender_email,
                from_name=project.default_sender_name,
            )
            await message_service.mark_sent(message, result.provider_message_id)
        except EmailSendError as exc:
            if message is not None:
                await message_service.mark_failed(
                    message, exc.detail, terminal=exc.permanent or final_attempt
                )
            if exc.permanent:
                # Retrying cannot help (rejected request, bad credentials, refused
                # recipient): fail for good instead of spending the retry budget.
                logger.error(
                    "permanent send failure for task %s: %s", broker_message_id, exc.detail
                )
                return
            raise
        except ValidationFailedError as exc:
            if message is not None:
                await message_service.mark_failed(message, exc.detail, terminal=final_attempt)
            raise
        except (NotFoundError, ConflictError):
            # Retried by Celery (autoretry_for); no row to update or nothing to record yet.
            raise
        except Exception as exc:
            # Anything unexpected (Blob/network errors, bugs) is not retried by Celery:
            # record it as a terminal failure instead of leaving the row stuck in
            # "processing" (DVB-344). The session may be unusable after a DB error.
            if message is not None:
                await session.rollback()
                await message_service.mark_failed(
                    message, f"unexpected error: {type(exc).__name__}", terminal=True
                )
            raise


async def record_rejection(
    broker_message_id: str,
    reason: str,
    project_id: Any,
    template_key: Any,
    to_email: Any,
    payload: Any,
) -> None:
    """Persists a task the worker could not parse (DVB-456), then disposes the pool
    for the same reason as process_message_isolated."""
    try:
        async with session_factory() as session:
            await RejectedMessageService(RejectedMessageRepository(session)).record(
                RejectedMessageCreate(
                    broker_message_id=broker_message_id,
                    reason=reason,
                    raw_project_id=project_id,
                    template_key=template_key,
                    to_email=to_email,
                    payload_keys=(
                        sorted(str(k) for k in payload) if isinstance(payload, dict) else []
                    ),
                )
            )
    finally:
        await engine.dispose()


async def process_message_isolated(*args: Any, **kwargs: Any) -> None:
    """Runs process_message, then disposes the module-level engine's pool.

    Every Celery task calls asyncio.run() -- a brand-new event loop -- but
    app.db.session builds one engine (pooled asyncpg connections) at import
    time. Connections left in the pool by a previous task are bound to that
    task's now-closed loop, so the next task in the same fork process died
    with "attached to a different loop" and the message was lost
    (acidevbutter/butter_relay#29). Disposing inside the same loop that used
    the pool gives each task a fresh one.
    """
    try:
        await process_message(*args, **kwargs)
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]  # celery ships no type information
    bind=True,
    name=TASK_NAME,
    autoretry_for=(NotFoundError, EmailSendError, ValidationFailedError, ConflictError),
    max_retries=MAX_RETRIES,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
)
def process_message_task(
    self: Any,
    *,
    project_id: str,
    template_key: str,
    to: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """self.request.id is a stable task id assigned by the producer at
    enqueue time (Celery preserves it across retries of the same logical
    message), so it plays the idempotency role the old SQS MessageId used to
    -- see Message.broker_message_id / get_by_broker_message_id.

    MalformedMessageError never retries -- bad input won't fix itself with
    time -- but it is recorded in rejected_messages and the task ends FAILURE
    instead of silently succeeding (DVB-456). A permanent provider error
    (EmailSendError.permanent) marks the message terminally failed and returns
    without retrying. Everything else in autoretry_for backs off exponentially (retry_backoff) up to
    MAX_RETRIES, then Celery marks the task FAILURE for good; Message.status is
    already FAILED in the DB from mark_failed by then (terminal_failure=True on
    the last try), so nothing vanishes silently. This replaces the old
    un-acked SQS message retrying every fixed 60s forever (no DLQ existed)
    with a bounded, backed-off retry that gives up loudly instead of
    spinning.
    """
    try:
        asyncio.run(
            process_message_isolated(
                self.request.id,
                project_id,
                template_key,
                to,
                payload,
                final_attempt=self.request.retries >= MAX_RETRIES,
            )
        )
    except MalformedMessageError as exc:
        # Retrying cannot fix bad input, but the task must not end as "succeeded":
        # record it in rejected_messages and let Celery mark it FAILURE (DVB-456).
        logger.error(
            "rejecting malformed relay message %s: %s",
            self.request.id,
            exc.detail,
            extra={"event": "relay_message_rejected", "broker_message_id": self.request.id},
        )
        asyncio.run(
            record_rejection(self.request.id, exc.detail, project_id, template_key, to, payload)
        )
        raise
