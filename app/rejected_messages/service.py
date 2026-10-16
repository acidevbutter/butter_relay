from app.core.decorators import log_errors
from app.rejected_messages.models import RejectedMessage
from app.rejected_messages.repository import RejectedMessageRepository
from app.rejected_messages.schemas import RejectedMessageCreate


@log_errors
class RejectedMessageService:
    def __init__(self, repository: RejectedMessageRepository):
        self.repository = repository

    async def list(self, limit: int = 100) -> list[RejectedMessage]:
        return await self.repository.list(limit)

    async def record(self, payload: RejectedMessageCreate) -> RejectedMessage:
        """Idempotent per task id: a redelivered malformed task keeps its first row."""
        existing = await self.repository.get_by_broker_message_id(payload.broker_message_id)
        if existing is not None:
            return existing
        return await self.repository.create(payload)
