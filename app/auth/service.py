import uuid

import bcrypt

from app.auth.models import AdminUser
from app.auth.repository import AdminUserRepository
from app.core.decorators import log_errors
from app.core.exceptions import UnauthorizedError


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


@log_errors
class AuthService:
    def __init__(self, repository: AdminUserRepository):
        self.repository = repository

    async def register(self, email: str, password: str) -> AdminUser:
        return await self.repository.create(email, _hash_password(password))

    async def authenticate(self, email: str, password: str) -> AdminUser:
        admin_user = await self.repository.get_by_email(email)
        if admin_user is None or not admin_user.is_active:
            raise UnauthorizedError("Invalid email or password")
        if not _verify_password(password, admin_user.password_hash):
            raise UnauthorizedError("Invalid email or password")
        await self.repository.record_login(admin_user)
        return admin_user

    async def get(self, admin_user_id: uuid.UUID) -> AdminUser | None:
        return await self.repository.get(admin_user_id)
