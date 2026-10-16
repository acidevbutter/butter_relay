from datetime import datetime

from sqlalchemy import func, select, update
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

    async def count_since(self, since: datetime) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(RejectedMessage)
            .where(RejectedMessage.received_at >= since)
        )
        return int(result.scalar_one())

    async def purge_recipients(self, before: datetime) -> int:
        result = await self.session.execute(
            update(RejectedMessage)
            .where(RejectedMessage.received_at < before, RejectedMessage.to_email.is_not(None))
            .values(to_email=None)
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        # UPDATE returns a CursorResult; Result's type hints don't expose rowcount.
        return int(getattr(result, "rowcount", 0) or 0)
