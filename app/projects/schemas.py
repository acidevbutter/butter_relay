import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.email_provider import DeliveryProvider

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _validate_slug(slug: str) -> str:
    if not _SLUG_PATTERN.match(slug):
        raise ValueError(
            "slug must be lowercase alphanumeric segments separated by single hyphens "
            "(it doubles as the project's Celery queue name)"
        )
    return slug


class DeliveryConfig(BaseModel):
    """Non-secret, provider-specific delivery options. Credentials never live
    here: Brevo uses the shared BREVO_API_KEY and the SMTP channel is unauthenticated.
    """

    model_config = ConfigDict(extra="forbid")

    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)


class ProjectCreate(BaseModel):
    name: str
    slug: str
    external: bool = False
    default_sender_email: EmailStr
    default_sender_name: str
    delivery_provider: DeliveryProvider = DeliveryProvider.BREVO
    delivery_config: DeliveryConfig = DeliveryConfig()

    _validate_slug = field_validator("slug")(_validate_slug)


class ProjectUpdate(BaseModel):
    name: str | None = None
    external: bool | None = None
    default_sender_email: EmailStr | None = None
    default_sender_name: str | None = None
    delivery_provider: DeliveryProvider | None = None
    delivery_config: DeliveryConfig | None = None
    is_active: bool | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    external: bool
    queue_name: str
    default_sender_email: str
    default_sender_name: str
    delivery_provider: DeliveryProvider
    delivery_config: dict[str, Any]
    is_active: bool
    created_at: datetime
