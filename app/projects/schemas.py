import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.email_provider import DeliveryProvider, SmtpSecurity

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _validate_slug(slug: str) -> str:
    if not _SLUG_PATTERN.match(slug):
        raise ValueError(
            "slug must be lowercase alphanumeric segments separated by single hyphens "
            "(it doubles as the project's Celery queue name)"
        )
    return slug


class DeliveryConfig(BaseModel):
    """Non-secret, provider-specific delivery options. Credentials never live here:
    the SMTP password and a per-project Brevo key are encrypted separately
    (ProjectSecretsUpdate, DVB-306)."""

    model_config = ConfigDict(extra="forbid")

    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: SmtpSecurity | None = None
    smtp_username: str | None = Field(default=None, max_length=255)
    timeout_seconds: float | None = Field(default=None, ge=1, le=60)


class ProjectSecretsUpdate(BaseModel):
    """Write-only delivery secrets. A field left out (or empty) keeps the stored value;
    names listed in `clear` are removed."""

    model_config = ConfigDict(extra="forbid")

    smtp_password: str | None = Field(default=None, max_length=500)
    brevo_api_key: str | None = Field(default=None, max_length=500)
    clear: list[str] = []


class ProjectCreate(BaseModel):
    name: str
    slug: str
    external: bool = False
    default_sender_email: EmailStr
    default_sender_name: str
    delivery_provider: DeliveryProvider = DeliveryProvider.BREVO
    delivery_config: DeliveryConfig = DeliveryConfig()
    allow_undeclared_types: bool = False

    _validate_slug = field_validator("slug")(_validate_slug)


class ProjectUpdate(BaseModel):
    name: str | None = None
    external: bool | None = None
    default_sender_email: EmailStr | None = None
    default_sender_name: str | None = None
    delivery_provider: DeliveryProvider | None = None
    delivery_config: DeliveryConfig | None = None
    allow_undeclared_types: bool | None = None
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
    # Secrets are never returned, only whether they are set.
    has_smtp_password: bool
    has_brevo_api_key: bool
    allow_undeclared_types: bool
    is_active: bool
    created_at: datetime


class ProjectDeliveryRead(BaseModel):
    """What a company's own API key may see of its delivery setup."""

    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID = Field(validation_alias="id")
    delivery_provider: DeliveryProvider
    delivery_config: dict[str, Any]
    default_sender_email: str
    default_sender_name: str


class ProjectDeliveryUpdate(BaseModel):
    """Only the channel choice is API-key writable. Host/port overrides (and the
    sender identity) stay admin-only: a leaked project key must not be able to
    point that project's mail at an arbitrary host."""

    delivery_provider: DeliveryProvider
