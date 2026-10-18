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
import json
import logging
import uuid
from typing import Any

from app.core.blob_client import BlobClient, get_blob_client
from app.core.decorators import log_call
from app.core.email_provider import Attachment, EmailProvider, EmailSendError, OutboundEmail
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.core.redaction import mask_email
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
    if payload is not None and not isinstance(payload, dict):
        raise MalformedMessageError("Malformed relay message: payload must be an object")
    size = len(json.dumps(payload or {}, default=str).encode())
    if size > settings.relay_max_payload_bytes:
        raise MalformedMessageError(
            f"Malformed relay message: payload is {size} bytes, "
            f"limit is {settings.relay_max_payload_bytes}"
        )
    return parsed_project_id, template_key, to_email, dict(payload or {})


async def _record_failure(
    exc: Exception,
    *,
    session: Any,
    message_service: MessageService,
    message: Message | None,
    final_attempt: bool,
    task_ref: str,
) -> bool:
    """One failure policy for both task shapes. Returns True when the caller must
    re-raise (Celery decides about the retry), False when the failure is final and the
    task should end quietly."""
    if isinstance(exc, EmailSendError):
        if message is not None:
            await message_service.mark_failed(
                message, exc.detail, terminal=exc.permanent or final_attempt
            )
        if exc.permanent:
            # Retrying cannot help (rejected request, bad credentials, refused
            # recipient): fail for good instead of spending the retry budget.
            logger.error("permanent send failure for task %s: %s", task_ref, exc.detail)
            return False
        return True
    if isinstance(exc, ValidationFailedError | NotFoundError):
        # Retried by Celery (autoretry_for); a stored message is marked failed so it
        # never sits in "pending" after the last try.
        if message is not None:
            await message_service.mark_failed(message, exc.detail, terminal=final_attempt)
        return True
    if isinstance(exc, ConflictError):
        return True
    # Anything unexpected (Blob/network errors, bugs) is not retried by Celery: record it
    # as a terminal failure instead of leaving the row stuck in "processing" (DVB-344).
    # The session may be unusable after a DB error.
    if message is not None:
        await session.rollback()
        # rollback expires loaded rows; reload before recording the attempt.
        await session.refresh(message)
        await message_service.mark_failed(
            message, f"unexpected error: {type(exc).__name__}", terminal=True
        )
    return True


async def _already_handled(message_service: MessageService, message: Message) -> bool:
    """True when a (re)delivered task must not send: already sent, failed for good, or
    stopped mid-send by a dead worker (DVB-188)."""
    if message.status == MessageStatus.SENT:
        return True
    if message.terminal_failure:
        # A redelivery of a message that already failed for good (permanent provider
        # error, blocked recipient, retries spent) must not send it again; an admin
        # can still resend it explicitly.
        logger.warning("ignoring redelivery of terminally failed message %s", message.id)
        return True
    if message.status == MessageStatus.PROCESSING:
        # Only a worker that died between "processing" and an outcome leaves a row
        # here (every handled failure ends in mark_failed). The provider may or may
        # not have accepted the e-mail, so sending again risks a duplicate: stop and
        # make it visible instead; an admin can resend after checking (DVB-188).
        detail = "delivery outcome unknown: a previous attempt stopped mid-send"
        logger.error("%s (message %s)", detail, message.id)
        await message_service.mark_failed(message, detail, terminal=True)
        return True
    return False


def _blocked_recipients(addresses: list[str]) -> list[str]:
    return [a for a in addresses if not settings.is_recipient_allowed(a)]


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
        if existing is not None and await _already_handled(message_service, existing):
            return

        message: Message | None = existing
        try:
            project = await project_service.get(parsed_project_id)
            if not project.is_active:
                # Used to return silently; now the message is visible as failed (DVB-309).
                logger.warning("message for inactive project %s", parsed_project_id)
                message = existing or await message_service.create(
                    MessageCreate(
                        project_id=parsed_project_id,
                        to_email=to_email,
                        payload=render_payload,
                        broker_message_id=broker_message_id,
                        provider=project.delivery_provider,
                    )
                )
                await message_service.mark_failed(message, "project is inactive", terminal=True)
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
                logger.warning("%s: %s", detail, mask_email(to_email))
                await message_service.mark_failed(message, detail, terminal=True)
                return

            provider = email_provider or project_service.provider_for(project)
            result = await provider.send(
                OutboundEmail(
                    to=[to_email],
                    subject=subject,
                    html=html,
                    from_email=project.default_sender_email,
                    from_name=project.default_sender_name,
                )
            )
            await message_service.mark_sent(message, result.provider_message_id)
        except Exception as exc:
            if await _record_failure(
                exc, session=session, message_service=message_service, message=message,
                final_attempt=final_attempt, task_ref=broker_message_id,
            ):
                raise


@log_call
async def process_stored_message(
    message_id: str,
    routing_key: str | None = None,
    email_provider: EmailProvider | None = None,
    final_attempt: bool = False,
) -> None:
    """New task shape (DVB-309): the Message (raw or template, recipients, attachments)
    was stored by the authenticated HTTP API; the task only carries its id. The project
    comes from that row, never from the producer, and must own the queue the task came
    from (DVB-204 / H-02)."""
    try:
        parsed_id = uuid.UUID(str(message_id))
    except ValueError as exc:
        raise MalformedMessageError(f"Malformed relay message: bad message_id: {exc}") from exc
    async with session_factory() as session:
        repository = MessageRepository(session)
        project_service = ProjectService(ProjectRepository(session))
        template_service = TemplateService(TemplateRepository(session))
        message_service = MessageService(repository)

        message = await repository.get_by_id(parsed_id)
        if message is None:
            raise MalformedMessageError(f"Malformed relay message: no stored message {parsed_id}")
        if await _already_handled(message_service, message):
            return
        try:
            project = await project_service.get(message.project_id)
            if routing_key and routing_key != project.queue_name:
                detail = (
                    f"task arrived on queue {routing_key!r}, not the project's "
                    f"{project.queue_name!r}"
                )
                logger.error("refusing message %s: %s", message.id, detail)
                await message_service.mark_failed(message, detail, terminal=True)
                return
            if not project.is_active:
                await message_service.mark_failed(message, "project is inactive", terminal=True)
                return

            if message.raw_html is not None:
                subject, html, text = message.raw_subject or "", message.raw_html, message.raw_text
            else:
                template = await template_service.get_active(
                    project.id, message.email_type or ""
                )
                subject, html = template_service.render(template, dict(message.payload or {}))
                text = None
            message = await message_service.mark_processing(message, subject)

            recipients = message.recipients or {"to": [message.to_email]}
            to = list(recipients.get("to") or [message.to_email])
            cc = list(recipients.get("cc") or [])
            bcc = list(recipients.get("bcc") or [])
            blocked = _blocked_recipients([*to, *cc, *bcc])
            if blocked:
                detail = "recipient blocked by non-production delivery policy: " + ", ".join(
                    mask_email(a) or "" for a in blocked
                )
                logger.warning(detail)
                await message_service.mark_failed(message, detail, terminal=True)
                return

            attachments = [
                Attachment(a.filename, a.content_type, a.data)
                for a in await repository.list_attachments(message.id)
            ]
            provider = email_provider or project_service.provider_for(project)
            result = await provider.send(
                OutboundEmail(
                    to=to,
                    cc=cc,
                    bcc=bcc,
                    reply_to=message.reply_to,
                    subject=subject,
                    html=html,
                    text=text,
                    headers=dict(message.headers or {}),
                    attachments=attachments,
                    from_email=project.default_sender_email,
                    from_name=project.default_sender_name,
                )
            )
            await message_service.mark_sent(message, result.provider_message_id)
            # Attachment bytes are personal data: gone as soon as they are delivered.
            await repository.delete_attachments(message.id)
        except Exception as exc:
            if await _record_failure(
                exc, session=session, message_service=message_service, message=message,
                final_attempt=final_attempt, task_ref=str(message_id),
            ):
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


async def process_stored_message_isolated(*args: Any, **kwargs: Any) -> None:
    try:
        await process_stored_message(*args, **kwargs)
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
    message_id: str | None = None,
    project_id: str | None = None,
    template_key: str | None = None,
    to: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Two task shapes share this name:

    - `message_id` (DVB-309): a Message stored by the authenticated HTTP API.
    - legacy `project_id/template_key/to/payload` (send_task straight to Redis). The
      producer's project_id is trusted here (H-02); ALLOW_LEGACY_SEND_TASK=false
      refuses this shape once every producer uses the HTTP API (DVB-204).

    self.request.id is a stable task id assigned by the producer at enqueue time
    (Celery preserves it across retries of the same logical message), so it plays the
    idempotency role -- see Message.broker_message_id / get_by_broker_message_id.

    MalformedMessageError never retries -- bad input won't fix itself with time --
    but it is recorded in rejected_messages and the task ends FAILURE instead of
    silently succeeding (DVB-456). A permanent provider error marks the message
    terminally failed and returns without retrying. Everything else in autoretry_for
    backs off exponentially up to MAX_RETRIES, then Celery marks the task FAILURE for
    good; Message.status is already FAILED by then (terminal_failure=True on the last
    try), so nothing vanishes silently.
    """
    final_attempt = self.request.retries >= MAX_RETRIES
    try:
        if message_id is not None:
            routing_key = (getattr(self.request, "delivery_info", None) or {}).get(
                "routing_key"
            )
            asyncio.run(
                process_stored_message_isolated(
                    message_id, routing_key, final_attempt=final_attempt
                )
            )
            return
        if not settings.allow_legacy_send_task:
            raise MalformedMessageError(
                "Legacy send task refused (ALLOW_LEGACY_SEND_TASK=false); "
                "send through POST /api/v1/messages/send"
            )
        asyncio.run(
            process_message_isolated(
                self.request.id,
                project_id,
                template_key,
                to,
                payload,
                final_attempt=final_attempt,
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
