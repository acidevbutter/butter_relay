import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_keys.models import ApiKey
from app.core.exceptions import ConflictError


class ApiKeyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_project(self, project_id: uuid.UUID) -> list[ApiKey]:
        result = await self.session.execute(
            select(ApiKey).where(ApiKey.project_id == project_id).order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        result = await self.session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        return result.scalar_one_or_none()

    async def create(
        self, project_id: uuid.UUID, label: str, key_hash: str, expires_at: datetime | None = None
    ) -> ApiKey:
        api_key = ApiKey(
            project_id=project_id, label=label, key_hash=key_hash, expires_at=expires_at
        )
        self.session.add(api_key)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("Generated API key collided, please retry") from exc
        await self.session.refresh(api_key)
        return api_key

    async def revoke(self, api_key: ApiKey) -> ApiKey:
        api_key.revoked_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(api_key)
        return api_key

    async def touch_last_used(self, api_key: ApiKey) -> None:
        api_key.last_used_at = datetime.now(UTC)
        await self.session.commit()
