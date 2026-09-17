import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from app.api_keys.models import ApiKey
from app.api_keys.repository import ApiKeyRepository
from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError, UnauthorizedError
from app.projects.repository import ProjectRepository

_KEY_PREFIX = "brk_"  # "butter relay key"
_EXTERNAL_KEY_TTL = timedelta(days=30)


def _hash_key(plaintext_key: str) -> str:
    return hashlib.sha256(plaintext_key.encode()).hexdigest()


@log_errors
class ApiKeyService:
    def __init__(self, repository: ApiKeyRepository, project_repository: ProjectRepository):
        self.repository = repository
        self.project_repository = project_repository

    async def list_for_project(self, project_id: uuid.UUID) -> list[ApiKey]:
        return await self.repository.list_for_project(project_id)

    async def create(self, project_id: uuid.UUID, label: str) -> tuple[ApiKey, str]:
        project = await self.project_repository.get(project_id)
        if project is None:
            raise NotFoundError(f"Project {project_id} not found")
        expires_at = datetime.now(UTC) + _EXTERNAL_KEY_TTL if project.external else None
        plaintext_key = _KEY_PREFIX + secrets.token_urlsafe(32)
        api_key = await self.repository.create(
            project_id, label, _hash_key(plaintext_key), expires_at
        )
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
        if api_key.expires_at is not None:
            # SQLite (used in tests) doesn't round-trip tzinfo the way Postgres does,
            # so a value read back can come out naive even though it was written aware.
            expires_at = api_key.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at < datetime.now(UTC):
                raise UnauthorizedError(f"API key expired at {expires_at.isoformat()}")
        await self.repository.touch_last_used(api_key)
        return api_key
