from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSession
from app.messages.repository import MessageRepository
from app.messages.service import MessageService


def get_message_service(db: DbSession) -> MessageService:
    return MessageService(MessageRepository(db))


MessageServiceDep = Annotated[MessageService, Depends(get_message_service)]
