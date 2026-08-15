import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.dependencies import DbSession
from app.messages.models import MessageStatus
from app.messages.repository import MessageRepository
from app.messages.schemas import MessageRead
from app.messages.service import MessageService


def get_message_service(db: DbSession) -> MessageService:
    return MessageService(MessageRepository(db))


MessageServiceDep = Annotated[MessageService, Depends(get_message_service)]

router = APIRouter(prefix="/api/v1/messages", tags=["messages"])


@router.get("/", response_model=list[MessageRead], summary="List messages (the email log)")
async def list_messages(
    service: MessageServiceDep,
    project_id: uuid.UUID | None = None,
    status_filter: MessageStatus | None = None,
    limit: int = 100,
) -> list[MessageRead]:
    return await service.list(project_id, status_filter, limit)


@router.get("/{message_id}", response_model=MessageRead, summary="Get a message")
async def get_message(message_id: uuid.UUID, service: MessageServiceDep) -> MessageRead:
    return await service.get(message_id)
