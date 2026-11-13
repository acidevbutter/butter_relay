import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PairingCode(Base):
    """One-time code that lets a client app (the WordPress plugin) trade for a send-scoped
    API key after a staff member approved the connection. Only the hash is stored."""

    __tablename__ = "pairing_codes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    site_host: Mapped[str] = mapped_column(String(255))
    approved_by: Mapped[str] = mapped_column(String(320))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectRequest(Base):
    """What a WordPress site sent (server to server, by POST) when asking to connect:
    who it is, where to return and the sender it wants. The browser only carries the
    request id; the consent page reads the rest from here. Only the id's hash is stored."""

    __tablename__ = "connect_requests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    site_host: Mapped[str] = mapped_column(String(255))
    site_name: Mapped[str] = mapped_column(String(200))
    site_url: Mapped[str] = mapped_column(String(500))
    sender_email: Mapped[str] = mapped_column(String(320))
    sender_name: Mapped[str] = mapped_column(String(200))
    return_url: Mapped[str] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
