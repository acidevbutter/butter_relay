"""Client manifest (DVB-310 / S5): everything an external client declares about itself,
applied idempotently by `PUT /api/v1/admin/clients/{slug}/manifest`."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.email_provider import DeliveryProvider
from app.email_types.schemas import EmailTypeCreate
from app.projects.schemas import DeliveryConfig
from app.templates_.schemas import _KEY_PATTERN


class ManifestSecrets(BaseModel):
    """Write-only. A field left out keeps the stored value; exports never contain it."""

    model_config = ConfigDict(extra="forbid")

    smtp_password: str | None = Field(default=None, max_length=500)
    brevo_api_key: str | None = Field(default=None, max_length=500)


class ManifestTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    subject_template: str = Field(min_length=1, max_length=500)
    html_template: str = Field(min_length=1, max_length=512 * 1024)
    variables_schema: dict[str, Any] = {}

    @field_validator("key")
    @classmethod
    def _validate_key(cls, key: str) -> str:
        if not _KEY_PATTERN.match(key):
            raise ValueError("invalid template key")
        return key


class ClientManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    external: bool = True
    default_sender_email: EmailStr
    default_sender_name: str = Field(min_length=1, max_length=200)
    delivery_provider: DeliveryProvider = DeliveryProvider.BREVO
    delivery_config: DeliveryConfig = DeliveryConfig()
    allow_undeclared_types: bool = False
    rate_per_minute: int | None = Field(default=None, ge=1, le=100_000)
    is_active: bool = True
    secrets: ManifestSecrets | None = None
    email_types: list[EmailTypeCreate] = []
    templates: list[ManifestTemplate] = []


class Outcome(BaseModel):
    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    pruned: list[str] = []


class ManifestApplyResult(BaseModel):
    dry_run: bool
    project: Literal["created", "updated", "unchanged"]
    project_changes: list[str] = []
    secrets_set: list[str] = []
    email_types: Outcome
    templates: Outcome

    @property
    def changed(self) -> bool:
        return (
            self.project != "unchanged"
            or bool(self.secrets_set)
            or any(
                (o.created or o.updated or o.pruned) for o in (self.email_types, self.templates)
            )
        )
