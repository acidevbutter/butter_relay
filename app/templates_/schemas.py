import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TemplateCreate(BaseModel):
    project_id: uuid.UUID
    key: str
    subject_template: str
    html_template: str
    variables_schema: dict = {}


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    key: str
    subject_template: str
    html_template: str
    variables_schema: dict
    version: int
    is_active: bool
    created_at: datetime


class TemplatePreviewRequest(BaseModel):
    payload: dict = {}


class TemplatePreviewResponse(BaseModel):
    subject: str
    html: str
