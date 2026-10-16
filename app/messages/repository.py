import builtins
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Text, Update, cast, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.messages.models import Message, MessageAttachment, MessageAttempt, MessageStatus
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

    def _record_attempt(
        self,
        message: Message,
        outcome: str,
        *,
        terminal: bool = False,
        error: str | None = None,
        provider_message_id: str | None = None,
    ) -> None:
        self.session.add(
            MessageAttempt(
                message_id=message.id,
                number=message.attempts,
                outcome=outcome,
                terminal=terminal,
                error=error,
                provider_message_id=provider_message_id,
            )
        )

    async def mark_sent(self, message: Message, provider_message_id: str) -> Message:
        message.status = MessageStatus.SENT
        message.terminal_failure = False
        message.provider_message_id = provider_message_id
        message.sent_at = datetime.now(UTC)
        self._record_attempt(message, "sent", provider_message_id=provider_message_id)
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def mark_failed(
        self, message: Message, error: str, *, terminal: bool = False
    ) -> Message:
        message.status = MessageStatus.FAILED
        message.last_error = error
        message.terminal_failure = terminal
        self._record_attempt(message, "failed", terminal=terminal, error=error)
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def list_attempts(self, message_id: uuid.UUID) -> builtins.list[MessageAttempt]:
        result = await self.session.execute(
            select(MessageAttempt)
            .where(MessageAttempt.message_id == message_id)
            .order_by(MessageAttempt.created_at, MessageAttempt.number)
        )
        return list(result.scalars().all())

    async def count_by_status(self, since: datetime) -> dict[str, int]:
        result = await self.session.execute(
            select(Message.status, func.count())
            .where(Message.received_at >= since)
            .group_by(Message.status)
        )
        return {str(status): int(count) for status, count in result.all()}

    async def count_terminal_failures(self, since: datetime) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.received_at >= since, Message.terminal_failure.is_(True))
        )
        return int(result.scalar_one())

    async def count_stuck_processing(self, older_than: datetime) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.status == MessageStatus.PROCESSING, Message.received_at < older_than
            )
        )
        return int(result.scalar_one())

    @staticmethod
    def purge_bodies_statement(before: datetime) -> Update:
        """Clears the payload of finished messages received before `before`. The row,
        status and subject stay for the delivery history."""
        return (
            update(Message)
            .where(
                Message.received_at < before,
                Message.status.in_([MessageStatus.SENT, MessageStatus.FAILED]),
                or_(
                    # Postgres has no "<>" for json; compare its text form (#73).
                    cast(Message.payload, Text) != "{}",
                    Message.raw_html.is_not(None),
                    Message.raw_text.is_not(None),
                ),
            )
            .values(payload={}, raw_html=None, raw_text=None)
            .execution_options(synchronize_session=False)
        )

    async def purge_bodies(self, before: datetime) -> int:
        result = await self.session.execute(self.purge_bodies_statement(before))
        await self.session.execute(
            delete(MessageAttachment).where(
                MessageAttachment.message_id.in_(
                    select(Message.id).where(
                        Message.received_at < before,
                        Message.status.in_([MessageStatus.SENT, MessageStatus.FAILED]),
                    )
                )
            )
        )
        await self.session.commit()
        # UPDATE returns a CursorResult; Result's type hints don't expose rowcount.
        return int(getattr(result, "rowcount", 0) or 0)

    # --- HTTP send API (DVB-309) ---------------------------------------------------

    async def get_by_idempotency_key(
        self, project_id: uuid.UUID, idempotency_key: str
    ) -> Message | None:
        result = await self.session.execute(
            select(Message).where(
                Message.project_id == project_id, Message.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()

    async def create_with_attachments(
        self, message: Message, attachments: builtins.list[MessageAttachment]
    ) -> Message:
        self.session.add(message)
        try:
            await self.session.flush()
            for attachment in attachments:
                attachment.message_id = message.id
                self.session.add(attachment)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("A message with this Idempotency-Key already exists") from exc
        await self.session.refresh(message)
        return message

    async def list_attachments(self, message_id: uuid.UUID) -> builtins.list[MessageAttachment]:
        result = await self.session.execute(
            select(MessageAttachment).where(MessageAttachment.message_id == message_id)
        )
        return list(result.scalars().all())

    async def delete_attachments(self, message_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(MessageAttachment).where(MessageAttachment.message_id == message_id)
        )
        await self.session.commit()

    async def mark_pending(self, message: Message) -> Message:
        message.status = MessageStatus.PENDING
        message.last_error = None
        message.terminal_failure = False
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def get_by_id(self, message_id: uuid.UUID) -> Message | None:
        return await self.session.get(Message, message_id)
