import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MessageStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # One row per logical message: the Celery task id is the idempotency key, so two
        # workers racing on the same (re)delivered task can never both create a row.
        UniqueConstraint(
            "project_id", "broker_message_id", name="uq_messages_project_broker_message_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("templates.id"), default=None)
    # Reserved for future delivery channels (in_app, push, ...) -- only "email" is
    # actually delivered today; see core/email_provider.py and the plan notes on scope.
    channel: Mapped[str] = mapped_column(String(20), default="email")
    # Which delivery provider handled it (the project's choice at send time).
    provider: Mapped[str | None] = mapped_column(String(20), default=None)
    to_email: Mapped[str] = mapped_column(String(320))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rendered_subject: Mapped[str | None] = mapped_column(String(500), default=None)
    status: Mapped[MessageStatus] = mapped_column(
        String(20), default=MessageStatus.PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    # True once no further automatic attempt will happen (permanent provider error, or
    # the retry budget is spent); a plain "failed" without it is still being retried.
    terminal_failure: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    provider_message_id: Mapped[str | None] = mapped_column(String(200), default=None)
    broker_message_id: Mapped[str | None] = mapped_column(String(200), default=None, index=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class MessageAttempt(Base):
    """One row per delivery attempt that reached an outcome (DVB-188): the message row
    only keeps the latest status/last_error, this keeps the history."""

    __tablename__ = "message_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    # Message.attempts at the time (0 = failed before rendering, e.g. Blob lookup).
    number: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(20))  # sent | failed
    terminal: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    error: Mapped[str | None] = mapped_column(Text, default=None)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
