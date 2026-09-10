import uuid

from fastapi import APIRouter, status

from app.api_keys.dependencies import ApiKeyServiceDep, CurrentApiKey
from app.api_keys.models import ApiKey
from app.api_keys.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from app.core.exceptions import ForbiddenError

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


def _require_same_project(current_api_key: ApiKey, project_id: uuid.UUID) -> None:
    """An API key may only manage keys belonging to its own project.
    See docs/auditoria-seguranca-2026-08-29.md (item 1) / docs/prompts/autenticar-api-v1.md.
    """
    if current_api_key.project_id != project_id:
        raise ForbiddenError("API key does not belong to this project")


@router.get("/", response_model=list[ApiKeyRead], summary="List API keys for a project")
async def list_api_keys(
    project_id: uuid.UUID, current_api_key: CurrentApiKey, service: ApiKeyServiceDep
) -> list[ApiKey]:
    _require_same_project(current_api_key, project_id)
    return await service.list_for_project(project_id)


@router.post(
    "/",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
)
async def create_api_key(
    payload: ApiKeyCreate, current_api_key: CurrentApiKey, service: ApiKeyServiceDep
) -> ApiKeyCreated:
    _require_same_project(current_api_key, payload.project_id)
    api_key, plaintext_key = await service.create(payload.project_id, payload.label)
    return ApiKeyCreated(id=api_key.id, label=api_key.label, plaintext_key=plaintext_key)


@router.post("/{api_key_id}/revoke", response_model=ApiKeyRead, summary="Revoke an API key")
async def revoke_api_key(
    api_key_id: uuid.UUID,
    project_id: uuid.UUID,
    current_api_key: CurrentApiKey,
    service: ApiKeyServiceDep,
) -> ApiKey:
    _require_same_project(current_api_key, project_id)
    return await service.revoke(api_key_id, project_id)
