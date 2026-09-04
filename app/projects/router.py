import uuid

from fastapi import APIRouter, status

from app.auth.dependencies import CurrentAdminUserApi
from app.projects.dependencies import ProjectServiceDep
from app.projects.schemas import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

# Projects are global (cross-tenant) entities -- creating/listing/reading/updating one is
# an admin action, not something a single project's own API key should be able to do.
# See docs/auditoria-seguranca-2026-08-29.md (item 1) and docs/prompts/autenticar-api-v1.md.


@router.get("/", response_model=list[ProjectRead], summary="List projects")
async def list_projects(
    admin_user: CurrentAdminUserApi, service: ProjectServiceDep
) -> list[ProjectRead]:
    return await service.list()


@router.post(
    "/", response_model=ProjectRead, status_code=status.HTTP_201_CREATED, summary="Create a project"
)
async def create_project(
    payload: ProjectCreate, admin_user: CurrentAdminUserApi, service: ProjectServiceDep
) -> ProjectRead:
    return await service.create(payload)


@router.get("/{project_id}", response_model=ProjectRead, summary="Get a project")
async def get_project(
    project_id: uuid.UUID, admin_user: CurrentAdminUserApi, service: ProjectServiceDep
) -> ProjectRead:
    return await service.get(project_id)


@router.patch("/{project_id}", response_model=ProjectRead, summary="Update a project")
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    admin_user: CurrentAdminUserApi,
    service: ProjectServiceDep,
) -> ProjectRead:
    return await service.update(project_id, payload)
