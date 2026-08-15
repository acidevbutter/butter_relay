import uuid

from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError
from app.messages.models import Message, MessageStatus
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

    async def create(self, payload: MessageCreate) -> Message:
        return await self.repository.create(payload)

    async def mark_processing(self, message: Message, rendered_subject: str) -> Message:
        return await self.repository.mark_processing(message, rendered_subject)

    async def mark_sent(self, message: Message, provider_message_id: str) -> Message:
        return await self.repository.mark_sent(message, provider_message_id)

    async def mark_failed(self, message: Message, error: str) -> Message:
        return await self.repository.mark_failed(message, error)
