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
from app.core.email_provider import EmailProvider, EmailSendError, get_email_provider
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.core.metrics import emit_metrics
from app.db.session import session_factory
from app.messages.models import Message, MessageStatus
from app.messages.repository import MessageRepository
from app.messages.schemas import MessageCreate
from app.messages.service import MessageService
from app.projects.repository import ProjectRepository
from app.projects.service import ProjectService
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
    email_provider: EmailProvider,
    blob_client: BlobClient | None = None,
) -> None:
    blob = blob_client or get_blob_client()
    async with session_factory() as session:
        project_service = ProjectService(ProjectRepository(session))
        template_service = TemplateService(TemplateRepository(session))
        message_service = MessageService(MessageRepository(session))

        try:
            parsed_project_id, template_key, to_email, render_payload = _parse_task_args(
                project_id, template_key, to_email, payload
            )
        except MalformedMessageError:
            await emit_metrics([("MessagesMalformed", 1, "Count")], {"TemplateKey": "unknown"})
            raise

        existing = await message_service.get_by_broker_message_id(broker_message_id)
        if existing is not None and existing.status == MessageStatus.SENT:
            await emit_metrics([("MessagesProcessed", 1, "Count")], {"TemplateKey": template_key})
            return

        dimensions = {"TemplateKey": template_key}
        await emit_metrics([("MessagesProcessed", 1, "Count")], dimensions)
        try:
            project = await project_service.get(parsed_project_id)
            if not project.is_active:
                logger.warning("skipping message for inactive project %s", parsed_project_id)
                await emit_metrics([("MessagesFailed", 1, "Count")], dimensions)
                return

            template = await template_service.get_active(parsed_project_id, template_key)
            blob_id = render_payload.get("blob_id")
            if blob_id:
                blob_meta = await blob.get(str(blob_id))
                public_url = blob_meta.get("public_url")
                if not isinstance(public_url, str) or not public_url:
                    raise ValidationFailedError("NFS-e blob has no public_url")
                render_payload["cta_link"] = public_url

            message: Message = await message_service.create(
                MessageCreate(
                    project_id=parsed_project_id,
                    template_id=template.id,
                    to_email=to_email,
                    payload=render_payload,
                    broker_message_id=broker_message_id,
                )
            )

            subject, html = template_service.render(template, render_payload)
            message = await message_service.mark_processing(message, subject)

            if not settings.is_recipient_allowed(to_email):
                detail = "recipient blocked by non-production delivery policy"
                logger.warning("%s: %s", detail, to_email)
                await message_service.mark_failed(message, detail)
                await emit_metrics([("MessagesFailed", 1, "Count")], dimensions)
                return

            result = await email_provider.send(
                to=to_email,
                subject=subject,
                html=html,
                from_email=project.default_sender_email,
                from_name=project.default_sender_name,
            )
            await message_service.mark_sent(message, result.provider_message_id)
            await emit_metrics([("MessagesSent", 1, "Count")], dimensions)
        except EmailSendError as exc:
            await message_service.mark_failed(message, exc.detail)
            await emit_metrics([("MessagesFailed", 1, "Count")], dimensions)
            raise
        except ValidationFailedError as exc:
            if "message" in locals():
                await message_service.mark_failed(message, exc.detail)
            await emit_metrics([("MessagesFailed", 1, "Count")], dimensions)
            raise
        except Exception:
            await emit_metrics([("MessagesFailed", 1, "Count")], dimensions)
            raise


@celery_app.task(  # type: ignore[untyped-decorator]  # celery ships no type information
    bind=True,
    name=TASK_NAME,
    autoretry_for=(NotFoundError, EmailSendError, ValidationFailedError),
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
    time, so it's logged and dropped. Everything in autoretry_for backs off
    exponentially (retry_backoff) up to MAX_RETRIES, then Celery marks the
    task FAILURE for good; Message.status is already FAILED in the DB from
    mark_failed by then, so nothing vanishes silently. This replaces the old
    un-acked SQS message retrying every fixed 60s forever (no DLQ existed)
    with a bounded, backed-off retry that gives up loudly instead of
    spinning.
    """
    email_provider = get_email_provider(settings)
    try:
        asyncio.run(
            process_message(
                self.request.id,
                project_id,
                template_key,
                to,
                payload,
                email_provider,
            )
        )
    except MalformedMessageError as exc:
        logger.error("dropping malformed relay message %s: %s", self.request.id, exc.detail)
        return
