import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.projects.models import Project
from app.projects.schemas import ProjectCreate, ProjectUpdate


class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self) -> list[Project]:
        result = await self.session.execute(select(Project).order_by(Project.created_at.desc()))
        return list(result.scalars().all())

    async def get(self, project_id: uuid.UUID) -> Project | None:
        return await self.session.get(Project, project_id)

    async def get_by_slug(self, slug: str) -> Project | None:
        result = await self.session.execute(select(Project).where(Project.slug == slug))
        return result.scalar_one_or_none()

    async def create(self, payload: ProjectCreate) -> Project:
        project = Project(**payload.model_dump())
        self.session.add(project)
        await self._commit_or_raise_conflict(payload.slug)
        await self.session.refresh(project)
        return project

    async def update(self, project: Project, payload: ProjectUpdate) -> Project:
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(project, field, value)
        await self._commit_or_raise_conflict(project.slug)
        await self.session.refresh(project)
        return project

    async def _commit_or_raise_conflict(self, slug: str) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(f"A project with slug {slug!r} already exists") from exc
