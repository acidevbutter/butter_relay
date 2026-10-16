import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

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
