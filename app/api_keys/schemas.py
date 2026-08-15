import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApiKeyCreate(BaseModel):
    project_id: uuid.UUID
    label: str


class ApiKeyCreated(BaseModel):
    """Returned only once, at creation time -- the plaintext key is never stored or shown again."""

    id: uuid.UUID
    label: str
    plaintext_key: str


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    label: str
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
