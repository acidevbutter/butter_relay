import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:limit] if text else None


class RejectedMessageCreate(BaseModel):
    broker_message_id: str
    reason: str
    raw_project_id: str | None = None
    template_key: str | None = None
    to_email: str | None = None
    payload_keys: list[str] = []

    # Whatever the producer sent is untrusted and may be any type or length.
    @field_validator("raw_project_id", "template_key", mode="before")
    @classmethod
    def _clip_200(cls, value: Any) -> str | None:
        return _clip(value, 200)

    @field_validator("to_email", mode="before")
    @classmethod
    def _clip_320(cls, value: Any) -> str | None:
        return _clip(value, 320)


class RejectedMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    broker_message_id: str
    reason: str
    raw_project_id: str | None
    template_key: str | None
    to_email: str | None
    payload_keys: list[str]
    received_at: datetime
