from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.rejected_messages.models import RejectedMessage
from app.rejected_messages.schemas import RejectedMessageCreate


class RejectedMessageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self, limit: int = 100) -> list[RejectedMessage]:
        result = await self.session.execute(
            select(RejectedMessage).order_by(RejectedMessage.received_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_broker_message_id(self, broker_message_id: str) -> RejectedMessage | None:
        result = await self.session.execute(
            select(RejectedMessage).where(RejectedMessage.broker_message_id == broker_message_id)
        )
        return result.scalar_one_or_none()

    async def create(self, payload: RejectedMessageCreate) -> RejectedMessage:
        rejected = RejectedMessage(**payload.model_dump())
        self.session.add(rejected)
        await self.session.commit()
        await self.session.refresh(rejected)
        return rejected
