"""Celery task: render -> send -> record, for jobs enqueued by devbutter_backend
(or any other producer) onto butter_relay's shared queue -- replaces the old
SQS long-poll consumer (app/worker/consumer.py, removed).

Business logic lives in the plain async `process_message` so it's testable
without Celery/Redis at all; `process_message_task` is the thin sync bridge
Celery actually calls (mirrors butter_blob's app/worker/tasks.py pattern).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.core.blob_client import BlobClient, get_blob_client
from app.core.decorators import log_call
from app.core.email_provider import BrevoEmailProvider, EmailSendError
from app.core.exceptions import ValidationFailedError
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


@log_call
async def process_message(
    *,
    project_id: uuid.UUID,
    template_key: str,
    to_email: str,
    payload: dict[str, Any],
    message_id: str,
    email_provider: BrevoEmailProvider,
    blob_client: BlobClient | None = None,
) -> None:
    """`message_id` dedupes retries of the *same* task delivery -- normally the
    Celery task id (see process_message_task below). It reuses the
    `sqs_message_id` column/field name (pre-dating this migration) as a
    transport-agnostic broker-message-id; renaming it is a separate change.
    """
    blob = blob_client or get_blob_client()
    async with session_factory() as session:
        project_service = ProjectService(ProjectRepository(session))
        template_service = TemplateService(TemplateRepository(session))
        message_service = MessageService(MessageRepository(session))

        existing = await message_service.get_by_sqs_message_id(message_id)
        if existing is not None and existing.status == MessageStatus.SENT:
            await emit_metrics([("MessagesProcessed", 1, "Count")], {"TemplateKey": template_key})
            return

        dimensions = {"TemplateKey": template_key}
        await emit_metrics([("MessagesProcessed", 1, "Count")], dimensions)
        try:
            project = await project_service.get(project_id)
            if not project.is_active:
                logger.warning("skipping message for inactive project %s", project_id)
                await emit_metrics([("MessagesFailed", 1, "Count")], dimensions)
                return

            template = await template_service.get_active(project_id, template_key)
            render_payload = dict(payload)
            blob_id = render_payload.get("blob_id")
            if blob_id:
                blob_meta = await blob.get(str(blob_id))
                public_url = blob_meta.get("public_url")
                if not isinstance(public_url, str) or not public_url:
                    raise ValidationFailedError("NFS-e blob has no public_url")
                render_payload["cta_link"] = public_url

            message: Message = await message_service.create(
                MessageCreate(
                    project_id=project_id,
                    template_id=template.id,
                    to_email=to_email,
                    payload=render_payload,
                    sqs_message_id=message_id,
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
    autoretry_for=(Exception,),
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
)
def process_message_task(
    self: Any, *, project_id: str, template_key: str, to: str, payload: dict[str, Any]
) -> None:
    asyncio.run(
        process_message(
            project_id=uuid.UUID(project_id),
            template_key=template_key,
            to_email=to,
            payload=payload,
            message_id=self.request.id,
            email_provider=BrevoEmailProvider(api_key=settings.brevo_api_key),
        )
    )
