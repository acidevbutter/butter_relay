import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer

from app.core.redaction import mask_email, redact
from app.messages.models import MessageStatus


class MessageCreate(BaseModel):
    project_id: uuid.UUID
    template_id: uuid.UUID | None = None
    to_email: str
    payload: dict[str, Any] = {}
    broker_message_id: str | None = None
    provider: str | None = None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    template_id: uuid.UUID | None
    channel: str
    provider: str | None
    to_email: str
    payload: dict[str, Any]
    rendered_subject: str | None
    status: MessageStatus
    attempts: int
    last_error: str | None
    terminal_failure: bool
    provider_message_id: str | None
    broker_message_id: str | None
    received_at: datetime
    sent_at: datetime | None


class MessagePublicRead(BaseModel):
    """What a project's API key may read back (DVB-208 / M-05): no payload (the rendered
    data is personal and transactional), the recipient masked, and the error already
    redacted at the source. The full row stays visible only in the authenticated admin.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    template_id: uuid.UUID | None
    channel: str
    provider: str | None
    to_email: str
    rendered_subject: str | None
    status: MessageStatus
    attempts: int
    last_error: str | None
    terminal_failure: bool
    provider_message_id: str | None
    broker_message_id: str | None
    received_at: datetime
    sent_at: datetime | None

    @field_serializer("to_email")
    def _mask_to_email(self, value: str) -> str:
        return mask_email(value) or ""

    @field_serializer("last_error")
    def _redact_last_error(self, value: str | None) -> str | None:
        # Rows written before DVB-208 may still hold a raw provider body.
        return redact(value)
