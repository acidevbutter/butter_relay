import builtins
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError
from app.messages.models import Message, MessageAttempt, MessageStatus
from app.messages.repository import MessageRepository
from app.messages.schemas import MessageCreate


@log_errors
class MessageService:
    def __init__(self, repository: MessageRepository):
        self.repository = repository

    async def list(
        self,
        project_id: uuid.UUID | None = None,
        status_filter: MessageStatus | None = None,
        limit: int = 100,
    ) -> list[Message]:
        return await self.repository.list(project_id, status_filter, limit)

    async def get(self, message_id: uuid.UUID) -> Message:
        message = await self.repository.get(message_id)
        if message is None:
            raise NotFoundError(f"Message {message_id} not found")
        return message

    async def get_by_broker_message_id(
        self, broker_message_id: str, project_id: uuid.UUID | None = None
    ) -> Message | None:
        return await self.repository.get_by_broker_message_id(broker_message_id, project_id)

    async def create(self, payload: MessageCreate) -> Message:
        return await self.repository.create(payload)

    async def mark_processing(
        self, message: Message, rendered_subject: str, payload: dict[str, Any] | None = None
    ) -> Message:
        return await self.repository.mark_processing(message, rendered_subject, payload)

    async def mark_sent(self, message: Message, provider_message_id: str) -> Message:
        return await self.repository.mark_sent(message, provider_message_id)

    async def mark_failed(
        self, message: Message, error: str, *, terminal: bool = False
    ) -> Message:
        return await self.repository.mark_failed(message, error, terminal=terminal)

    async def list_attempts(self, message_id: uuid.UUID) -> builtins.list[MessageAttempt]:
        return await self.repository.list_attempts(message_id)

    async def stats(self, window: timedelta = timedelta(hours=24)) -> dict[str, Any]:
        """Minimum delivery health numbers for the admin dashboard (DVB-188)."""
        now = datetime.now(UTC)
        since = now - window
        return {
            "by_status": await self.repository.count_by_status(since),
            "terminal_failures": await self.repository.count_terminal_failures(since),
            # A message still "processing" after 15 min means a worker died mid-send.
            "stuck_processing": await self.repository.count_stuck_processing(
                now - timedelta(minutes=15)
            ),
        }

    async def purge_bodies(self, retention_days: int) -> int:
        return await self.repository.purge_bodies(
            datetime.now(UTC) - timedelta(days=retention_days)
        )
