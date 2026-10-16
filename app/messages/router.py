import uuid

from fastapi import APIRouter, Query

from app.api_keys.dependencies import CurrentApiKey
from app.core.exceptions import ForbiddenError
from app.messages.dependencies import MessageServiceDep
from app.messages.models import Message, MessageStatus
from app.messages.schemas import MessageRead

router = APIRouter(prefix="/api/v1/messages", tags=["messages"])


@router.get("/", response_model=list[MessageRead], summary="List messages (the email log)")
async def list_messages(
    current_api_key: CurrentApiKey,
    service: MessageServiceDep,
    status_filter: MessageStatus | None = None,
    limit: int = Query(100, ge=1, le=200),
) -> list[Message]:
    # Scope is forced to the caller's own project -- an API key must never be able to
    # read another project's relayed messages by passing a different project_id.
    # See docs/auditoria-seguranca-2026-08-29.md (item 1) / docs/prompts/autenticar-api-v1.md.
    return await service.list(current_api_key.project_id, status_filter, limit)


@router.get("/{message_id}", response_model=MessageRead, summary="Get a message")
async def get_message(
    message_id: uuid.UUID, current_api_key: CurrentApiKey, service: MessageServiceDep
) -> Message:
    message = await service.get(message_id)
    if message.project_id != current_api_key.project_id:
        # 403, not 404: existence of the id is not itself sensitive, ownership is.
        raise ForbiddenError("API key does not belong to this project")
    return message
