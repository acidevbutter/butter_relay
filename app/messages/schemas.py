import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.messages.models import MessageStatus


class MessageCreate(BaseModel):
    project_id: uuid.UUID
    template_id: uuid.UUID | None = None
    to_email: str
    payload: dict = {}
    sqs_message_id: str | None = None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    template_id: uuid.UUID | None
    channel: str
    to_email: str
    payload: dict
    rendered_subject: str | None
    status: MessageStatus
    attempts: int
    last_error: str | None
    provider_message_id: str | None
    sqs_message_id: str | None
    received_at: datetime
    sent_at: datetime | None
