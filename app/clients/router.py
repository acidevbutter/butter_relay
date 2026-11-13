import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from app.api_keys.dependencies import ApiKeyServiceDep
from app.api_keys.models import ApiKey
from app.api_keys.router import _created
from app.api_keys.schemas import ApiKeyCreated, ApiKeyRead, ApiKeyRotate
from app.auth.dependencies import AdminApiAccess
from app.clients.schemas import ClientManifest, ManifestApplyResult
from app.clients.service import ManifestService
from app.core.dependencies import DbSession
from app.projects.dependencies import ProjectServiceDep
from app.projects.models import Project
from app.projects.schemas import ProjectCreate, ProjectRead

router = APIRouter(prefix="/api/v1/admin", tags=["admin-clients"])


class ClientKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=200)
    scopes: list[str] | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


def get_manifest_service(db: DbSession) -> ManifestService:
    return ManifestService(db)


ManifestServiceDep = Annotated[ManifestService, Depends(get_manifest_service)]


@router.post("/clients", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ProjectCreate, _access: AdminApiAccess, service: ProjectServiceDep
) -> Project:
    return await service.create(payload)


@router.get("/clients/{slug}", response_model=ProjectRead)
async def get_client(slug: str, _access: AdminApiAccess, service: ProjectServiceDep) -> Project:
    return await service.get_by_slug(slug)


@router.get("/clients/{slug}/keys", response_model=list[ApiKeyRead])
async def list_client_keys(
    slug: str, _access: AdminApiAccess, projects: ProjectServiceDep, keys: ApiKeyServiceDep
) -> list[ApiKey]:
    return await keys.list_for_project((await projects.get_by_slug(slug)).id)


@router.post(
    "/clients/{slug}/keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED
)
async def create_client_key(
    slug: str,
    payload: ClientKeyCreate,
    _access: AdminApiAccess,
    projects: ProjectServiceDep,
    keys: ApiKeyServiceDep,
) -> ApiKeyCreated:
    """The plaintext key is in this response only; it is never stored or shown again."""
    project = await projects.get_by_slug(slug)
    api_key, plaintext = await keys.create(
        project.id, payload.label, scopes=payload.scopes, expires_in_days=payload.expires_in_days
    )
    return _created(api_key, plaintext)


@router.post("/clients/{slug}/keys/{api_key_id}/revoke", response_model=ApiKeyRead)
async def revoke_client_key(
    slug: str,
    api_key_id: uuid.UUID,
    _access: AdminApiAccess,
    projects: ProjectServiceDep,
    keys: ApiKeyServiceDep,
) -> ApiKey:
    return await keys.revoke(api_key_id, (await projects.get_by_slug(slug)).id)


@router.post(
    "/clients/{slug}/keys/{api_key_id}/rotate",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
)
async def rotate_client_key(
    slug: str,
    api_key_id: uuid.UUID,
    payload: ApiKeyRotate,
    _access: AdminApiAccess,
    projects: ProjectServiceDep,
    keys: ApiKeyServiceDep,
) -> ApiKeyCreated:
    api_key, plaintext = await keys.rotate(
        api_key_id,
        (await projects.get_by_slug(slug)).id,
        grace_hours=payload.grace_hours,
        expires_in_days=payload.expires_in_days,
    )
    return _created(api_key, plaintext)


@router.get("/manifest/schema", summary="JSON Schema of the client manifest")
async def manifest_schema(_access: AdminApiAccess) -> dict[str, Any]:
    return ClientManifest.model_json_schema()


@router.get("/clients/{slug}/manifest", response_model=ClientManifest, response_model_by_alias=True)
async def export_manifest(
    slug: str, _access: AdminApiAccess, projects: ProjectServiceDep, manifests: ManifestServiceDep
) -> ClientManifest:
    return await manifests.export(await projects.get_by_slug(slug))


@router.put("/clients/{slug}/manifest", response_model=ManifestApplyResult)
async def apply_manifest(
    slug: str,
    manifest: ClientManifest,
    _access: AdminApiAccess,
    manifests: ManifestServiceDep,
    dry_run: bool = False,
    prune: bool = False,
) -> ManifestApplyResult:
    """Idempotent and atomic: applying the same manifest twice changes nothing; a
    failure leaves the client untouched. `dry_run` returns the diff without writing;
    `prune` deactivates e-mail types/templates missing from the manifest. Errors are
    422 with one entry per offending path."""
    return await manifests.apply(slug, manifest, dry_run=dry_run, prune=prune)
