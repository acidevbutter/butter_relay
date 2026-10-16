import uuid

from fastapi import APIRouter, status

from app.api_keys.dependencies import ManageApiKey, ReadApiKey
from app.api_keys.models import ApiKey
from app.auth.dependencies import CurrentAdminUserApi
from app.core.exceptions import ForbiddenError
from app.projects.dependencies import ProjectServiceDep
from app.projects.models import Project
from app.projects.schemas import (
    ProjectCreate,
    ProjectDeliveryRead,
    ProjectDeliveryUpdate,
    ProjectRead,
    ProjectUpdate,
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

# Projects are global (cross-tenant) entities -- creating/listing/reading/updating one is
# an admin action, not something a single project's own API key should be able to do.
# See docs/auditoria-seguranca-2026-08-29.md (item 1) and docs/prompts/autenticar-api-v1.md.


@router.get("/", response_model=list[ProjectRead], summary="List projects")
async def list_projects(
    admin_user: CurrentAdminUserApi, service: ProjectServiceDep
) -> list[Project]:
    return await service.list()


@router.post(
    "/", response_model=ProjectRead, status_code=status.HTTP_201_CREATED, summary="Create a project"
)
async def create_project(
    payload: ProjectCreate, admin_user: CurrentAdminUserApi, service: ProjectServiceDep
) -> Project:
    return await service.create(payload)


@router.get("/{project_id}", response_model=ProjectRead, summary="Get a project")
async def get_project(
    project_id: uuid.UUID, admin_user: CurrentAdminUserApi, service: ProjectServiceDep
) -> Project:
    return await service.get(project_id)


@router.patch("/{project_id}", response_model=ProjectRead, summary="Update a project")
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    admin_user: CurrentAdminUserApi,
    service: ProjectServiceDep,
) -> Project:
    return await service.update(project_id, payload)


# --- Delivery channel, scoped to the caller's own project ---------------------------
# The DevButter admin (or any owning app) manages *its* company's channel with the
# project's API key -- no admin session needed, and no access to other projects.


def _require_own_project(current_api_key: ApiKey, project_id: uuid.UUID) -> None:
    if current_api_key.project_id != project_id:
        raise ForbiddenError("API key does not belong to this project")


@router.get(
    "/{project_id}/delivery",
    response_model=ProjectDeliveryRead,
    summary="Read this project's delivery channel (API key)",
)
async def get_delivery(
    project_id: uuid.UUID, current_api_key: ReadApiKey, service: ProjectServiceDep
) -> Project:
    _require_own_project(current_api_key, project_id)
    return await service.get(project_id)


@router.put(
    "/{project_id}/delivery",
    response_model=ProjectDeliveryRead,
    summary="Choose this project's delivery channel (API key)",
)
async def set_delivery(
    project_id: uuid.UUID,
    payload: ProjectDeliveryUpdate,
    current_api_key: ManageApiKey,
    service: ProjectServiceDep,
) -> Project:
    _require_own_project(current_api_key, project_id)
    return await service.update(
        project_id, ProjectUpdate(delivery_provider=payload.delivery_provider)
    )
