import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RejectedMessage(Base):
    """A task the worker could not even parse (bad project id, missing template key or
    recipient). It cannot live in `messages` -- that row needs a real Project -- so it
    lands here instead of vanishing as a "succeeded" task (DVB-456). The payload itself
    is not stored, only its keys, to keep PII out of a table nobody owns yet.
    """

    __tablename__ = "rejected_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    broker_message_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    reason: Mapped[str] = mapped_column(Text)
    raw_project_id: Mapped[str | None] = mapped_column(String(200), default=None)
    template_key: Mapped[str | None] = mapped_column(String(200), default=None)
    to_email: Mapped[str | None] = mapped_column(String(320), default=None)
    payload_keys: Mapped[list[Any]] = mapped_column(JSON, default=list)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
