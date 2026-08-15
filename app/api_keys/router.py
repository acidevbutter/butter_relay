import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api_keys.repository import ApiKeyRepository
from app.api_keys.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from app.api_keys.service import ApiKeyService
from app.core.dependencies import DbSession


def get_api_key_service(db: DbSession) -> ApiKeyService:
    return ApiKeyService(ApiKeyRepository(db))


ApiKeyServiceDep = Annotated[ApiKeyService, Depends(get_api_key_service)]

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


@router.get("/", response_model=list[ApiKeyRead], summary="List API keys for a project")
async def list_api_keys(project_id: uuid.UUID, service: ApiKeyServiceDep) -> list[ApiKeyRead]:
    return await service.list_for_project(project_id)


@router.post(
    "/",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key",
)
async def create_api_key(payload: ApiKeyCreate, service: ApiKeyServiceDep) -> ApiKeyCreated:
    api_key, plaintext_key = await service.create(payload.project_id, payload.label)
    return ApiKeyCreated(id=api_key.id, label=api_key.label, plaintext_key=plaintext_key)


@router.post("/{api_key_id}/revoke", response_model=ApiKeyRead, summary="Revoke an API key")
async def revoke_api_key(
    api_key_id: uuid.UUID, project_id: uuid.UUID, service: ApiKeyServiceDep
) -> ApiKeyRead:
    return await service.revoke(api_key_id, project_id)
