import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
