import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.projects.models import Project
from app.projects.schemas import ProjectCreate, ProjectUpdate


def _dump(payload: ProjectCreate | ProjectUpdate, *, exclude_unset: bool = False) -> dict[str, Any]:
    """JSON-mode dump (enums become plain strings for the DB), with unset
    delivery_config keys dropped so the stored JSON only holds real overrides."""
    data = payload.model_dump(mode="json", exclude_unset=exclude_unset)
    if isinstance(data.get("delivery_config"), dict):
        config = data["delivery_config"]
        data["delivery_config"] = {k: v for k, v in config.items() if v is not None}
    return data


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
        project = Project(**_dump(payload))
        self.session.add(project)
        await self._commit_or_raise_conflict(payload.slug)
        await self.session.refresh(project)
        return project

    async def update(self, project: Project, payload: ProjectUpdate) -> Project:
        for field, value in _dump(payload, exclude_unset=True).items():
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

    async def set_secrets(self, project: Project, token: str | None) -> Project:
        project.delivery_secrets_enc = token
        await self.session.commit()
        await self.session.refresh(project)
        return project
