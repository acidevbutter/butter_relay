import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.messages.models import Message, MessageStatus
from app.messages.schemas import MessageCreate


class MessageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(
        self,
        project_id: uuid.UUID | None = None,
        status_filter: MessageStatus | None = None,
        limit: int = 100,
    ) -> list[Message]:
        query = select(Message).order_by(Message.received_at.desc()).limit(limit)
        if project_id is not None:
            query = query.where(Message.project_id == project_id)
        if status_filter is not None:
            query = query.where(Message.status == status_filter)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get(self, message_id: uuid.UUID) -> Message | None:
        return await self.session.get(Message, message_id)

    async def get_by_broker_message_id(
        self, broker_message_id: str, project_id: uuid.UUID | None = None
    ) -> Message | None:
        # .first(), not scalar_one_or_none(): rows duplicated by the pre-#35 retry
        # behaviour must not crash the lookup.
        query = select(Message).where(Message.broker_message_id == broker_message_id)
        if project_id is not None:
            query = query.where(Message.project_id == project_id)
        result = await self.session.execute(query.order_by(Message.received_at.desc()))
        return result.scalars().first()

    async def create(self, payload: MessageCreate) -> Message:
        message = Message(**payload.model_dump())
        self.session.add(message)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                f"Message for task {payload.broker_message_id!r} already exists for this project"
            ) from exc
        await self.session.refresh(message)
        return message

    async def mark_processing(
        self, message: Message, rendered_subject: str, payload: dict[str, Any] | None = None
    ) -> Message:
        message.status = MessageStatus.PROCESSING
        message.terminal_failure = False
        message.rendered_subject = rendered_subject
        if payload is not None:
            message.payload = payload
        message.attempts += 1
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def mark_sent(self, message: Message, provider_message_id: str) -> Message:
        message.status = MessageStatus.SENT
        message.provider_message_id = provider_message_id
        message.sent_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def mark_failed(
        self, message: Message, error: str, *, terminal: bool = False
    ) -> Message:
        message.status = MessageStatus.FAILED
        message.last_error = error
        message.terminal_failure = terminal
        await self.session.commit()
        await self.session.refresh(message)
        return message
