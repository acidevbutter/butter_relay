import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.email_types.models import EmailType
from app.email_types.schemas import EmailTypeCreate


class EmailTypeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_project(
        self, project_id: uuid.UUID, *, active_only: bool = False
    ) -> list[EmailType]:
        query = select(EmailType).where(EmailType.project_id == project_id)
        if active_only:
            query = query.where(EmailType.is_active.is_(True))
        result = await self.session.execute(query.order_by(EmailType.key))
        return list(result.scalars().all())

    async def get(self, email_type_id: uuid.UUID) -> EmailType | None:
        return await self.session.get(EmailType, email_type_id)

    async def get_by_key(self, project_id: uuid.UUID, key: str) -> EmailType | None:
        result = await self.session.execute(
            select(EmailType).where(EmailType.project_id == project_id, EmailType.key == key)
        )
        return result.scalar_one_or_none()

    async def create(self, project_id: uuid.UUID, payload: EmailTypeCreate) -> EmailType:
        email_type = EmailType(project_id=project_id, **payload.model_dump(mode="json"))
        self.session.add(email_type)
        await self.session.commit()
        await self.session.refresh(email_type)
        return email_type

    async def update(self, email_type: EmailType, values: dict[str, object]) -> EmailType:
        for field, value in values.items():
            setattr(email_type, field, value)
        await self.session.commit()
        await self.session.refresh(email_type)
        return email_type
