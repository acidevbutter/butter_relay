import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.templates_.models import Template


class TemplateRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_project(self, project_id: uuid.UUID) -> list[Template]:
        result = await self.session.execute(
            select(Template)
            .where(Template.project_id == project_id)
            .order_by(Template.key, Template.version.desc())
        )
        return list(result.scalars().all())

    async def get(self, template_id: uuid.UUID) -> Template | None:
        return await self.session.get(Template, template_id)

    async def get_active(self, project_id: uuid.UUID, key: str) -> Template | None:
        result = await self.session.execute(
            select(Template).where(
                Template.project_id == project_id, Template.key == key, Template.is_active.is_(True)
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_version(self, project_id: uuid.UUID, key: str) -> Template | None:
        result = await self.session.execute(
            select(Template)
            .where(Template.project_id == project_id, Template.key == key)
            .order_by(Template.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def deactivate(self, template: Template) -> None:
        template.is_active = False
        self.session.add(template)

    async def activate(self, template: Template) -> Template:
        template.is_active = True
        await self.session.commit()
        await self.session.refresh(template)
        return template

    async def create(self, template: Template) -> Template:
        self.session.add(template)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                f"Template {template.key!r} v{template.version} already exists for this project"
            ) from exc
        await self.session.refresh(template)
        return template
