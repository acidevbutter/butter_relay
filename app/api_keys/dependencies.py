from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header

from app.api_keys.models import SCOPE_MANAGE, SCOPE_READ, SCOPE_SEND, ApiKey
from app.api_keys.repository import ApiKeyRepository
from app.api_keys.service import ApiKeyService
from app.core import rate_limit
from app.core.dependencies import DbSession
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.projects.repository import ProjectRepository
from app.settings.config import settings


def get_api_key_service(db: DbSession) -> ApiKeyService:
    return ApiKeyService(ApiKeyRepository(db), ProjectRepository(db))


ApiKeyServiceDep = Annotated[ApiKeyService, Depends(get_api_key_service)]


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
    service = ApiKeyService(ApiKeyRepository(db), ProjectRepository(db))
    api_key = await service.authenticate(plaintext_key)
    project = await ProjectRepository(db).get(api_key.project_id)
    limit = (
        project.rate_per_minute
        if project is not None and project.rate_per_minute
        else settings.relay_default_rate_per_minute
    )
    await rate_limit.check(str(api_key.id), limit)
    return api_key


CurrentApiKey = Annotated[ApiKey, Depends(get_current_api_key)]


def require_scope(scope: str) -> Callable[[ApiKey], Awaitable[ApiKey]]:
    """403 unless the authenticated key carries `scope` (DVB-308)."""

    async def dependency(api_key: CurrentApiKey) -> ApiKey:
        if scope not in (api_key.scopes or []):
            raise ForbiddenError(f"API key lacks the {scope!r} scope")
        return api_key

    return dependency


SendApiKey = Annotated[ApiKey, Depends(require_scope(SCOPE_SEND))]
ReadApiKey = Annotated[ApiKey, Depends(require_scope(SCOPE_READ))]
ManageApiKey = Annotated[ApiKey, Depends(require_scope(SCOPE_MANAGE))]
