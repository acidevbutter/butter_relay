import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MessageStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("templates.id"), default=None)
    # Reserved for future delivery channels (in_app, push, ...) -- only "email" is
    # actually delivered today; see core/email_provider.py and the plan notes on scope.
    channel: Mapped[str] = mapped_column(String(20), default="email")
    to_email: Mapped[str] = mapped_column(String(320))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rendered_subject: Mapped[str | None] = mapped_column(String(500), default=None)
    status: Mapped[MessageStatus] = mapped_column(
        String(20), default=MessageStatus.PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), default=None)
    sqs_message_id: Mapped[str | None] = mapped_column(String(200), default=None, index=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
