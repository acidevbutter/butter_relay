import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.dependencies import DbSession
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from app.projects.service import ProjectService


def get_project_service(db: DbSession) -> ProjectService:
    return ProjectService(ProjectRepository(db))


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.get("/", response_model=list[ProjectRead], summary="List projects")
async def list_projects(service: ProjectServiceDep) -> list[ProjectRead]:
    return await service.list()


@router.post(
    "/", response_model=ProjectRead, status_code=status.HTTP_201_CREATED, summary="Create a project"
)
async def create_project(payload: ProjectCreate, service: ProjectServiceDep) -> ProjectRead:
    return await service.create(payload)


@router.get("/{project_id}", response_model=ProjectRead, summary="Get a project")
async def get_project(project_id: uuid.UUID, service: ProjectServiceDep) -> ProjectRead:
    return await service.get(project_id)


@router.patch("/{project_id}", response_model=ProjectRead, summary="Update a project")
async def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate, service: ProjectServiceDep
) -> ProjectRead:
    return await service.update(project_id, payload)
