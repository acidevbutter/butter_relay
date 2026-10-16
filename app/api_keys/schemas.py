import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Scope = Literal["send", "read", "manage"]


class ApiKeyCreate(BaseModel):
    project_id: uuid.UUID
    label: str = Field(min_length=1, max_length=200)
    # None = the project's default (all scopes; send+read for external projects).
    scopes: list[Scope] | None = None
    # None = never expires (DVB-308 removed the fixed 30 days for external projects).
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyRotate(BaseModel):
    """The new key keeps label and scopes. The old one keeps working until it is
    revoked, or for `grace_hours` when given (then it expires)."""

    grace_hours: int | None = Field(default=None, ge=0, le=24 * 90)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyCreated(BaseModel):
    """Returned only once, at creation time -- the plaintext key is never stored or shown again."""

    id: uuid.UUID
    label: str
    scopes: list[str]
    plaintext_key: str
    expires_at: datetime | None


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    label: str
    scopes: list[str]
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
