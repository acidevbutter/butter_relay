import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.api_keys.dependencies import ReadApiKey, SendApiKey
from app.core.exceptions import ForbiddenError, PayloadTooLargeError
from app.messages.dependencies import MessageSendServiceDep, MessageServiceDep
from app.messages.models import Message, MessageStatus
from app.messages.schemas import (
    MAX_BODY_BYTES,
    MessagePublicRead,
    MessageSendRequest,
    MessageSendResponse,
)

router = APIRouter(prefix="/api/v1/messages", tags=["messages"])


async def limit_body_size(request: Request) -> None:
    """413 before the body is parsed (DVB-309): Content-Length first, then the real size."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise PayloadTooLargeError("request body is larger than 3 MB")
    if len(await request.body()) > MAX_BODY_BYTES:
        raise PayloadTooLargeError("request body is larger than 3 MB")


@router.post(
    "/send",
    response_model=MessageSendResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send an e-mail (template or raw) over HTTPS",
    dependencies=[Depends(limit_body_size)],
    responses={
        200: {"description": "Same Idempotency-Key and body: the existing message"},
        409: {"description": "Idempotency-Key reused with a different body"},
        413: {"description": "Body, HTML or attachments over the limits"},
        422: {"description": "Undeclared type (code unknown_email_type), bad payload, ..."},
        429: {"description": "Rate limit (Retry-After)"},
        503: {"description": "Stored but not queued; retry with the same Idempotency-Key"},
    },
)
async def send_message(
    payload: MessageSendRequest,
    response: Response,
    current_api_key: SendApiKey,
    service: MessageSendServiceDep,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=100)
    ] = None,
) -> MessageSendResponse:
    """`project_id` is never read from the body: it is the API key's project (H-02).
    The message is stored before it is queued, then answered with 202 and its id."""
    message, accepted_now = await service.send(
        current_api_key.project_id, payload, idempotency_key
    )
    if not accepted_now:
        response.status_code = status.HTTP_200_OK
    return MessageSendResponse(
        message_id=message.id, status=message.status, type=message.email_type or payload.type
    )


@router.get("/", response_model=list[MessagePublicRead], summary="List messages (the email log)")
async def list_messages(
    current_api_key: ReadApiKey,
    service: MessageServiceDep,
    status_filter: MessageStatus | None = None,
    limit: int = Query(100, ge=1, le=200),
) -> list[Message]:
    # Scope is forced to the caller's own project -- an API key must never be able to
    # read another project's relayed messages by passing a different project_id.
    # See docs/auditoria-seguranca-2026-08-29.md (item 1) / docs/prompts/autenticar-api-v1.md.
    return await service.list(current_api_key.project_id, status_filter, limit)


@router.get("/{message_id}", response_model=MessagePublicRead, summary="Get a message")
async def get_message(
    message_id: uuid.UUID, current_api_key: ReadApiKey, service: MessageServiceDep
) -> Message:
    message = await service.get(message_id)
    if message.project_id != current_api_key.project_id:
        # 403, not 404: existence of the id is not itself sensitive, ownership is.
        raise ForbiddenError("API key does not belong to this project")
    return message
