"""Public JSON endpoint of the pairing flow: trade a one-time code for a send key."""

from fastapi import APIRouter, Request, status

from app.api_keys.models import ApiKey
from app.core import rate_limit
from app.core.exceptions import ValidationFailedError
from app.pairing.dependencies import PairingServiceDep
from app.pairing.schemas import (
    ConnectRequestCreate,
    ConnectRequestCreated,
    ExchangeRequest,
    ExchangeResponse,
)
from app.pairing.service import REQUEST_TTL_MINUTES

router = APIRouter(prefix="/api/v1/connect", tags=["pairing"])

_EXCHANGE_PER_MINUTE = 20
_REQUESTS_PER_MINUTE = 10


@router.post(
    "/requests",
    response_model=ConnectRequestCreated,
    status_code=status.HTTP_201_CREATED,
    summary="A site asks to connect (its data goes here by POST, not in the URL)",
)
async def create_connect_request(
    payload: ConnectRequestCreate, request: Request, service: PairingServiceDep
) -> ConnectRequestCreated:
    # Public: it only stores a pending request that a staff member still has to approve.
    client = request.client.host if request.client else "unknown"
    await rate_limit.check(f"connect-req:{client}", _REQUESTS_PER_MINUTE)
    token = await service.create_request(payload)
    return ConnectRequestCreated(request_id=token, expires_in_seconds=REQUEST_TTL_MINUTES * 60)


@router.post("/exchange", response_model=ExchangeResponse, summary="Trade a pairing code for a key")
async def exchange_code(
    payload: ExchangeRequest, request: Request, service: PairingServiceDep
) -> ExchangeResponse:
    # Unauthenticated by design (the code is the secret), so brute force is limited per IP.
    client = request.client.host if request.client else "unknown"
    await rate_limit.check(f"pairing:{client}", _EXCHANGE_PER_MINUTE)
    if not payload.code.startswith("brp_"):
        raise ValidationFailedError("Malformed code")
    api_key, plaintext_key, project_slug = await service.exchange(payload.code)
    return _response(api_key, plaintext_key, project_slug)


def _response(api_key: ApiKey, plaintext_key: str, project_slug: str) -> ExchangeResponse:
    return ExchangeResponse(
        api_key_id=api_key.id,
        plaintext_key=plaintext_key,
        scopes=list(api_key.scopes),
        project_slug=project_slug,
    )
