from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_keys.models import ApiKey
from app.api_keys.repository import ApiKeyRepository
from app.api_keys.service import ApiKeyService
from app.core.exceptions import UnauthorizedError
from app.db.session import get_db_session

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


async def get_current_api_key(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> ApiKey:
    """Authenticates a request to /api/v1/* via `Authorization: Bearer <key>`.

    See docs/auditoria-seguranca-2026-08-29.md (item 1) and
    docs/prompts/autenticar-api-v1.md -- the API previously had no auth at all.
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise UnauthorizedError("Missing or malformed Authorization header")
    plaintext_key = authorization.removeprefix("Bearer ").strip()
    if not plaintext_key:
        raise UnauthorizedError("Missing or malformed Authorization header")
    service = ApiKeyService(ApiKeyRepository(db))
    return await service.authenticate(plaintext_key)


CurrentApiKey = Annotated[ApiKey, Depends(get_current_api_key)]
