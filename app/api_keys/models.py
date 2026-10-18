import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SCOPE_SEND = "send"
SCOPE_READ = "read"
SCOPE_MANAGE = "manage"
ALL_SCOPES = (SCOPE_SEND, SCOPE_READ, SCOPE_MANAGE)
# Keys for external clients (projects marked external) can send and read, not manage.
EXTERNAL_DEFAULT_SCOPES = (SCOPE_SEND, SCOPE_READ)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(200))
    # What the key may do (DVB-308): "send" (POST messages), "read" (GET), "manage"
    # (keys, templates, delivery). Keys created before scopes existed keep all three.
    scopes: Mapped[list[str]] = mapped_column(JSON, default=lambda: list(ALL_SCOPES))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
