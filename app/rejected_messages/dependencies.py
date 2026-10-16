from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSession
from app.rejected_messages.repository import RejectedMessageRepository
from app.rejected_messages.service import RejectedMessageService


def get_rejected_message_service(db: DbSession) -> RejectedMessageService:
    return RejectedMessageService(RejectedMessageRepository(db))


RejectedMessageServiceDep = Annotated[
    RejectedMessageService, Depends(get_rejected_message_service)
]
