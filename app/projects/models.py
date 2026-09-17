import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def queue_name(self) -> str:
        return queue_name_for(self.slug)
