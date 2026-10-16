import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,149}$")


class TemplateCreate(BaseModel):
    project_id: uuid.UUID
    key: str
    # Limits (DVB-188): subject fits Message.rendered_subject; HTML matches the planned
    # 512 KB upload cap.
    subject_template: str = Field(min_length=1, max_length=500)
    html_template: str = Field(min_length=1, max_length=512 * 1024)
    variables_schema: dict[str, Any] = {}

    @field_validator("key")
    @classmethod
    def _validate_key(cls, key: str) -> str:
        if not _KEY_PATTERN.match(key):
            raise ValueError(
                "key must start with a letter or digit and contain only letters, digits, "
                "'_', '-' or '.' (max 150 chars)"
            )
        return key


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    key: str
    subject_template: str
    html_template: str
    variables_schema: dict[str, Any]
    version: int
    is_active: bool
    created_at: datetime


class TemplatePreviewRequest(BaseModel):
    payload: dict[str, Any] = {}


class TemplatePreviewResponse(BaseModel):
    subject: str
    html: str
