import hashlib
import secrets
import uuid

from app.api_keys.models import ApiKey
from app.api_keys.repository import ApiKeyRepository
from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError, UnauthorizedError

_KEY_PREFIX = "brk_"  # "butter relay key"


def _hash_key(plaintext_key: str) -> str:
    return hashlib.sha256(plaintext_key.encode()).hexdigest()


@log_errors
class ApiKeyService:
    def __init__(self, repository: ApiKeyRepository):
        self.repository = repository

    async def list_for_project(self, project_id: uuid.UUID) -> list[ApiKey]:
        return await self.repository.list_for_project(project_id)

    async def create(self, project_id: uuid.UUID, label: str) -> tuple[ApiKey, str]:
        plaintext_key = _KEY_PREFIX + secrets.token_urlsafe(32)
        api_key = await self.repository.create(project_id, label, _hash_key(plaintext_key))
        return api_key, plaintext_key

    async def revoke(self, api_key_id: uuid.UUID, project_id: uuid.UUID) -> ApiKey:
        keys = await self.repository.list_for_project(project_id)
        match = next((k for k in keys if k.id == api_key_id), None)
        if match is None:
            raise NotFoundError(f"API key {api_key_id} not found for this project")
        return await self.repository.revoke(match)

    async def authenticate(self, plaintext_key: str) -> ApiKey:
        api_key = await self.repository.get_by_hash(_hash_key(plaintext_key))
        if api_key is None or api_key.revoked_at is not None:
            raise UnauthorizedError("Invalid or revoked API key")
        await self.repository.touch_last_used(api_key)
        return api_key
