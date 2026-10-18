import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailType(Base):
    """A kind of e-mail a project (client) declares it sends (DVB-307 / S2). The relay
    knows exactly which types each client uses; sending an undeclared one is refused
    unless the project allows undeclared types. A `template`/`both` type renders the
    active template with the same key."""

    __tablename__ = "email_types"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_email_types_project_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    category: Mapped[str] = mapped_column(String(20), default="transactional")
    mode: Mapped[str] = mapped_column(String(10), default="template")
    locale: Mapped[str | None] = mapped_column(String(20), default=None)
    sample_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
