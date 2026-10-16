import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.email_provider import DeliveryProvider
from app.core.secrets import secret_flags
from app.db.base import Base


def queue_name_for(slug: str) -> str:
    """The Celery queue a project's messages are published/consumed on.

    Derived from the (immutable) slug rather than stored, so there is one
    source of truth and no migration needed if the naming scheme changes.
    """
    return f"relay-{slug}"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    external: Mapped[bool] = mapped_column(Boolean, default=False)
    default_sender_email: Mapped[str] = mapped_column(String(320))
    default_sender_name: Mapped[str] = mapped_column(String(200))
    # How this company's e-mail leaves the relay (see core/email_provider.py).
    # Brevo is the default; delivery_config holds non-secret channel options
    # (today: smtp_host / smtp_port for the "smtp" provider).
    delivery_provider: Mapped[str] = mapped_column(
        String(20),
        default=DeliveryProvider.BREVO.value,
        server_default=DeliveryProvider.BREVO.value,
    )
    delivery_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Fernet-encrypted JSON {"smtp_password": ..., "brevo_api_key": ...} (DVB-306).
    # Write-only: never serialised; read only by the provider factory.
    delivery_secrets_enc: Mapped[str | None] = mapped_column(Text, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def queue_name(self) -> str:
        return queue_name_for(self.slug)

    @property
    def has_smtp_password(self) -> bool:
        return secret_flags(self.delivery_secrets_enc)["has_smtp_password"]

    @property
    def has_brevo_api_key(self) -> bool:
        return secret_flags(self.delivery_secrets_enc)["has_brevo_api_key"]
