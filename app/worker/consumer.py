"""SQS long-poll consumer: receive -> render -> send -> ack.

Runs as its own process (see __main__.py), sharing the same models/services as
the API. boto3 has no native asyncio support, so SQS calls are pushed to a
thread via asyncio.to_thread while the render/send/DB-write pipeline for each
message stays async, matching the rest of the codebase.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import boto3  # type: ignore[import-untyped]

from app.api_keys.repository import (
    ApiKeyRepository,  # noqa: F401  (SQS auth is IAM, not API key -- see plan)
)
from app.core.blob_client import BlobClient, get_blob_client
from app.core.decorators import log_call
from app.core.email_provider import BrevoEmailProvider, EmailSendError
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

logger = logging.getLogger(__name__)


def build_sqs_client() -> Any:
    return boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.sqs_endpoint_url or None,
    )


class MalformedMessageError(Exception):
    def __init__(self, detail: str):
        self.detail = detail


def _parse_sqs_message(sqs_message: dict[str, Any]) -> tuple[uuid.UUID, str, str, dict[str, Any]]:
    try:
        body = json.loads(sqs_message["Body"])
        project_id = uuid.UUID(sqs_message["MessageAttributes"]["project_id"]["StringValue"])
        template_key = body["template_key"]
        to_email = body["to"]
        payload = body.get("payload", {})
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise MalformedMessageError(f"Malformed SQS message: {exc}") from exc
    return project_id, template_key, to_email, payload


@log_call  # type: ignore[untyped-decorator]
async def process_sqs_message(
    sqs_message: dict[str, Any],
    email_provider: BrevoEmailProvider,
    blob_client: BlobClient | None = None,
) -> None:
    blob = blob_client or get_blob_client()
    async with session_factory() as session:
        project_service = ProjectService(ProjectRepository(session))
        template_service = TemplateService(TemplateRepository(session))
        message_service = MessageService(MessageRepository(session))

        try:
            project_id, template_key, to_email, payload = _parse_sqs_message(sqs_message)
        except MalformedMessageError:
            await emit_metrics([("MessagesMalformed", 1, "Count")], {"TemplateKey": "unknown"})
            raise

        sqs_id = sqs_message.get("MessageId")
        if isinstance(sqs_id, str) and sqs_id:
            existing = await message_service.get_by_sqs_message_id(sqs_id)
            if existing is not None and existing.status == MessageStatus.SENT:
                await emit_metrics(
                    [("MessagesProcessed", 1, "Count")], {"TemplateKey": template_key}
                )
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
                    sqs_message_id=sqs_message.get("MessageId"),
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


async def run_forever() -> None:
    sqs = build_sqs_client()
    email_provider = BrevoEmailProvider(api_key=settings.brevo_api_key)
    logger.info("worker started, polling %s", settings.sqs_queue_url)

    while True:
        response = await asyncio.to_thread(
            sqs.receive_message,
            QueueUrl=settings.sqs_queue_url,
            MaxNumberOfMessages=settings.sqs_max_messages,
            WaitTimeSeconds=settings.sqs_wait_time_seconds,
            VisibilityTimeout=settings.sqs_visibility_timeout,
            MessageAttributeNames=["All"],
        )
        for sqs_message in response.get("Messages", []):
            try:
                await process_sqs_message(sqs_message, email_provider)
            except (MalformedMessageError, NotFoundError, EmailSendError, ValidationFailedError):
                # Recorded in Message.status / logged by @log_call already;
                # leave the SQS message un-deleted so visibility-timeout expiry
                # retries it, and after maxReceiveCount SQS's redrive policy
                # moves it to the DLQ (infra-level, not app code).
                continue
            else:
                await asyncio.to_thread(
                    sqs.delete_message,
                    QueueUrl=settings.sqs_queue_url,
                    ReceiptHandle=sqs_message["ReceiptHandle"],
                )
