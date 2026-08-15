import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AdminUser
from app.core.exceptions import ConflictError


class AdminUserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, admin_user_id: uuid.UUID) -> AdminUser | None:
        return await self.session.get(AdminUser, admin_user_id)

    async def get_by_email(self, email: str) -> AdminUser | None:
        result = await self.session.execute(select(AdminUser).where(AdminUser.email == email))
        return result.scalar_one_or_none()

    async def create(self, email: str, password_hash: str) -> AdminUser:
        admin_user = AdminUser(email=email, password_hash=password_hash)
        self.session.add(admin_user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(f"An admin user with email {email!r} already exists") from exc
        await self.session.refresh(admin_user)
        return admin_user

    async def record_login(self, admin_user: AdminUser) -> None:
        admin_user.last_login_at = datetime.now(UTC)
        await self.session.commit()
