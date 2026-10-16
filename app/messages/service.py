import base64
import binascii
import builtins
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.decorators import log_errors
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PayloadTooLargeError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from app.email_types.schemas import EmailTypeMode
from app.email_types.service import EmailTypeService
from app.messages.models import Message, MessageAttachment, MessageAttempt, MessageStatus
from app.messages.repository import MessageRepository
from app.messages.schemas import (
    MAX_ATTACHMENTS_BYTES,
    MAX_HTML_BYTES,
    MessageCreate,
    MessageSendRequest,
)
from app.projects.service import ProjectService
from app.templates_.service import TemplateService


@log_errors
class MessageService:
    def __init__(self, repository: MessageRepository):
        self.repository = repository

    async def list(
        self,
        project_id: uuid.UUID | None = None,
        status_filter: MessageStatus | None = None,
        limit: int = 100,
    ) -> list[Message]:
        return await self.repository.list(project_id, status_filter, limit)

    async def get(self, message_id: uuid.UUID) -> Message:
        message = await self.repository.get(message_id)
        if message is None:
            raise NotFoundError(f"Message {message_id} not found")
        return message

    async def get_by_broker_message_id(
        self, broker_message_id: str, project_id: uuid.UUID | None = None
    ) -> Message | None:
        return await self.repository.get_by_broker_message_id(broker_message_id, project_id)

    async def create(self, payload: MessageCreate) -> Message:
        return await self.repository.create(payload)

    async def mark_processing(
        self, message: Message, rendered_subject: str, payload: dict[str, Any] | None = None
    ) -> Message:
        return await self.repository.mark_processing(message, rendered_subject, payload)

    async def mark_sent(self, message: Message, provider_message_id: str) -> Message:
        return await self.repository.mark_sent(message, provider_message_id)

    async def mark_failed(
        self, message: Message, error: str, *, terminal: bool = False
    ) -> Message:
        return await self.repository.mark_failed(message, error, terminal=terminal)

    async def list_attempts(self, message_id: uuid.UUID) -> builtins.list[MessageAttempt]:
        return await self.repository.list_attempts(message_id)

    async def stats(self, window: timedelta = timedelta(hours=24)) -> dict[str, Any]:
        """Minimum delivery health numbers for the admin dashboard (DVB-188)."""
        now = datetime.now(UTC)
        since = now - window
        return {
            "by_status": await self.repository.count_by_status(since),
            "terminal_failures": await self.repository.count_terminal_failures(since),
            # A message still "processing" after 15 min means a worker died mid-send.
            "stuck_processing": await self.repository.count_stuck_processing(
                now - timedelta(minutes=15)
            ),
        }

    async def purge_bodies(self, retention_days: int) -> int:
        return await self.repository.purge_bodies(
            datetime.now(UTC) - timedelta(days=retention_days)
        )


# --- HTTP send API (DVB-309 / S4) ------------------------------------------------


def request_fingerprint(request: MessageSendRequest) -> str:
    """Canonical hash of a send request: same body -> same idempotent message."""
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def enqueue_message(message_id: uuid.UUID, queue: str) -> None:
    """Hands the stored message to the worker on the project's own queue. The task id is
    the message id, so the worker finds the pre-created row (no duplicate)."""
    from app.worker.celery_app import TASK_NAME, celery_app

    celery_app.send_task(
        TASK_NAME, kwargs={"message_id": str(message_id)}, task_id=str(message_id), queue=queue
    )


ENQUEUE_FAILED = "enqueue_failed"


@log_errors
class MessageSendService:
    """POST /api/v1/messages/send: validates against the client's declared e-mail types
    and templates, stores the Message *before* enqueueing, and is idempotent per
    Idempotency-Key. The project always comes from the API key (closes H-02 for HTTP
    producers)."""

    def __init__(
        self,
        repository: MessageRepository,
        project_service: ProjectService,
        email_type_service: EmailTypeService,
        template_service: TemplateService,
        enqueue: Callable[[uuid.UUID, str], None] | None = None,
    ):
        self.repository = repository
        self.project_service = project_service
        self.email_type_service = email_type_service
        self.template_service = template_service
        self.enqueue = enqueue

    async def send(
        self,
        project_id: uuid.UUID,
        request: MessageSendRequest,
        idempotency_key: str | None,
    ) -> tuple[Message, bool]:
        """Returns (message, accepted_now). accepted_now is False when an earlier request
        with the same Idempotency-Key and body already created it (no new e-mail)."""
        project = await self.project_service.get(project_id)
        if not project.is_active:
            raise ForbiddenError("This client is inactive")
        fingerprint = request_fingerprint(request)

        if idempotency_key:
            existing = await self.repository.get_by_idempotency_key(project_id, idempotency_key)
            if existing is not None:
                return await self._replay(existing, fingerprint, project.queue_name)

        mode = EmailTypeMode.RAW if request.is_raw else EmailTypeMode.TEMPLATE
        await self.email_type_service.resolve_for_send(
            project_id, request.type, mode, allow_undeclared=project.allow_undeclared_types
        )
        template_id: uuid.UUID | None = None
        if request.is_raw:
            if len((request.html or "").encode()) > MAX_HTML_BYTES:
                raise PayloadTooLargeError("html is larger than 1 MB")
        else:
            try:
                template = await self.template_service.get_active(project_id, request.type)
            except NotFoundError as exc:
                raise ValidationFailedError(
                    f"No active template for type {request.type!r}", code="template_missing"
                ) from exc
            # Renders once to reject a payload that would fail in the worker (422 now
            # instead of a failed message later).
            self.template_service.render(template, dict(request.payload))
            template_id = template.id

        attachments = self._decode_attachments(request)
        message_id = uuid.uuid4()
        message = Message(
            id=message_id,
            project_id=project_id,
            template_id=template_id,
            to_email=str(request.to[0]),
            payload=dict(request.payload),
            status=MessageStatus.PENDING,
            provider=project.delivery_provider,
            broker_message_id=str(message_id),
            idempotency_key=idempotency_key,
            request_hash=fingerprint,
            email_type=request.type,
            recipients={
                "to": [str(a) for a in request.to],
                "cc": [str(a) for a in request.cc],
                "bcc": [str(a) for a in request.bcc],
            },
            reply_to=str(request.reply_to) if request.reply_to else None,
            raw_subject=request.subject,
            raw_html=request.html,
            raw_text=request.text,
            headers=dict(request.headers) or None,
            source=str(request.metadata.get("source"))[:200] if request.metadata.get("source")
            else None,
            client_metadata=dict(request.metadata) or None,
        )
        try:
            message = await self.repository.create_with_attachments(message, attachments)
        except ConflictError:
            # Lost a race with the same Idempotency-Key: answer like the winner did.
            existing = await self.repository.get_by_idempotency_key(
                project_id, idempotency_key or ""
            )
            if existing is None:
                raise
            return await self._replay(existing, fingerprint, project.queue_name)
        await self._enqueue_or_fail(message, project.queue_name)
        return message, True

    async def _replay(
        self, existing: Message, fingerprint: str, queue: str
    ) -> tuple[Message, bool]:
        if existing.request_hash != fingerprint:
            raise ConflictError("Idempotency-Key was already used with a different request")
        if existing.status == MessageStatus.FAILED and existing.last_error == ENQUEUE_FAILED:
            # The first attempt never reached the queue: retrying with the same key
            # enqueues the stored message instead of creating a second one.
            existing = await self.repository.mark_pending(existing)
            await self._enqueue_or_fail(existing, queue)
            return existing, True
        return existing, False

    async def _enqueue_or_fail(self, message: Message, queue: str) -> None:
        try:
            # Resolved at call time so tests can swap the module-level function.
            (self.enqueue or enqueue_message)(message.id, queue)
        except Exception as exc:  # broker down (kombu/redis errors), never a 500
            await self.repository.mark_failed(message, ENQUEUE_FAILED)
            raise ServiceUnavailableError(
                "The message was stored but could not be queued; retry with the same "
                "Idempotency-Key",
                retry_after=30,
            ) from exc

    @staticmethod
    def _decode_attachments(request: MessageSendRequest) -> builtins.list[MessageAttachment]:
        decoded: builtins.list[MessageAttachment] = []
        total = 0
        for item in request.attachments:
            try:
                data = base64.b64decode(item.content_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValidationFailedError(
                    f"attachment {item.filename!r} is not valid base64", code="invalid_attachment"
                ) from exc
            total += len(data)
            if total > MAX_ATTACHMENTS_BYTES:
                raise PayloadTooLargeError("attachments are larger than 2 MB in total")
            decoded.append(
                MessageAttachment(
                    filename=item.filename, content_type=item.content_type,
                    size=len(data), data=data,
                )
            )
        return decoded
