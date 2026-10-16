"""Retention of personal data in the delivery log (DVB-208 / LGPD).

`purge_expired_bodies()` clears payloads of finished messages and recipients of
rejected tasks older than MESSAGE_BODY_RETENTION_DAYS. Run it daily, either with the
Celery task below under `celery beat`, or as a scheduled command:

    python -m scripts.purge_message_bodies
"""

from __future__ import annotations

import asyncio
import logging

from app.db.session import engine, session_factory
from app.messages.repository import MessageRepository
from app.messages.service import MessageService
from app.rejected_messages.repository import RejectedMessageRepository
from app.rejected_messages.service import RejectedMessageService
from app.settings.config import settings
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

PURGE_TASK_NAME = "butter_relay.purge_message_bodies"


async def purge_expired_bodies(retention_days: int | None = None) -> dict[str, int]:
    days = settings.message_body_retention_days if retention_days is None else retention_days
    try:
        async with session_factory() as session:
            messages = await MessageService(MessageRepository(session)).purge_bodies(days)
            rejected = await RejectedMessageService(
                RejectedMessageRepository(session)
            ).purge_recipients(days)
    finally:
        await engine.dispose()
    result = {"messages": messages, "rejected": rejected, "retention_days": days}
    logger.info("purged expired message bodies: %s", result, extra={"event": "retention_purge"})
    return result


@celery_app.task(name=PURGE_TASK_NAME)  # type: ignore[untyped-decorator]  # celery ships no type information
def purge_message_bodies_task() -> dict[str, int]:
    return asyncio.run(purge_expired_bodies())
