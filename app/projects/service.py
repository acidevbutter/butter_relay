import uuid

from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError
from app.projects.models import Project
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate, ProjectUpdate
from app.worker.queues import add_consumer_for_project


@log_errors
class ProjectService:
    def __init__(self, repository: ProjectRepository):
        self.repository = repository

    async def list(self) -> list[Project]:
        return await self.repository.list()

    async def get(self, project_id: uuid.UUID) -> Project:
        project = await self.repository.get(project_id)
        if project is None:
            raise NotFoundError(f"Project {project_id} not found")
        return project

    async def get_by_slug(self, slug: str) -> Project:
        project = await self.repository.get_by_slug(slug)
        if project is None:
            raise NotFoundError(f"Project with slug {slug!r} not found")
        return project

    async def create(self, payload: ProjectCreate) -> Project:
        project = await self.repository.create(payload)
        add_consumer_for_project(project)
        return project

    async def update(self, project_id: uuid.UUID, payload: ProjectUpdate) -> Project:
        project = await self.get(project_id)
        return await self.repository.update(project, payload)
