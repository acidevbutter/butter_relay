import enum
import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,99}$")


class EmailCategory(str, enum.Enum):
    TRANSACTIONAL = "transactional"
    MARKETING = "marketing"
    SYSTEM = "system"


class EmailTypeMode(str, enum.Enum):
    TEMPLATE = "template"  # payload rendered by the relay's template
    RAW = "raw"  # subject/html sent as given by the client
    BOTH = "both"


def _validate_key(key: str) -> str:
    if not _KEY_PATTERN.match(key):
        raise ValueError(
            "key must be 2-100 chars of lowercase letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return key


class EmailTypeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    description: str | None = Field(default=None, max_length=500)
    category: EmailCategory = EmailCategory.TRANSACTIONAL
    mode: EmailTypeMode = EmailTypeMode.TEMPLATE
    locale: str | None = Field(default=None, max_length=20)
    sample_payload: dict[str, Any] = {}
    is_active: bool = True

    _validate_key = field_validator("key")(_validate_key)


class EmailTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    description: str | None
    category: EmailCategory
    mode: EmailTypeMode
    locale: str | None
    sample_payload: dict[str, Any]
    is_active: bool
    created_at: datetime
