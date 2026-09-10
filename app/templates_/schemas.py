import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TemplateCreate(BaseModel):
    project_id: uuid.UUID
    key: str
    subject_template: str
    html_template: str
    variables_schema: dict[str, Any] = {}


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
