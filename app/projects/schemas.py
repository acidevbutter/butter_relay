import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class ProjectCreate(BaseModel):
    name: str
    slug: str
    sqs_queue_url: str
    default_sender_email: EmailStr
    default_sender_name: str


class ProjectUpdate(BaseModel):
    name: str | None = None
    sqs_queue_url: str | None = None
    default_sender_email: EmailStr | None = None
    default_sender_name: str | None = None
    is_active: bool | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    sqs_queue_url: str
    default_sender_email: str
    default_sender_name: str
    is_active: bool
    created_at: datetime
