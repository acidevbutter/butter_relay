import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from app.api_keys.models import SCOPE_SEND, ApiKey
from app.api_keys.service import ApiKeyService
from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError, UnauthorizedError, ValidationFailedError
from app.pairing.repository import PairingRepository
from app.projects.repository import ProjectRepository

_CODE_PREFIX = "brp_"  # "butter relay pairing"
CODE_TTL_MINUTES = 10
CALLBACK_PATH = "/wp-admin/admin-post.php"
CALLBACK_ACTION = "ediy_relay_connect_callback"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def validate_return_url(return_url: str) -> str:
    """The code is only handed to a WordPress plugin callback over https (or localhost
    in development), never to an arbitrary page. Returns the site host."""
    parsed = urlparse(return_url)
    host = parsed.hostname or ""
    local = host in {"localhost", "127.0.0.1"}
    scheme_ok = parsed.scheme == "https" or (parsed.scheme == "http" and local)
    action = parse_qs(parsed.query).get("action", [""])[0]
    if not (scheme_ok and host and parsed.path == CALLBACK_PATH and action == CALLBACK_ACTION):
        raise ValidationFailedError("return_url must be the plugin's https admin-post callback")
    return host


@log_errors
class PairingService:
    def __init__(
        self,
        repository: PairingRepository,
        project_repository: ProjectRepository,
        api_key_service: ApiKeyService,
    ):
        self.repository = repository
        self.project_repository = project_repository
        self.api_key_service = api_key_service

    async def approve(
        self, project_id: uuid.UUID, label: str, site_host: str, approved_by: str
    ) -> str:
        """Staff approved the connection of `site_host` to a project. Returns the code
        (shown once, in the redirect back to the site)."""
        project = await self.project_repository.get(project_id)
        if project is None or not project.is_active:
            raise NotFoundError(f"Project {project_id} not found")
        code = _CODE_PREFIX + secrets.token_urlsafe(32)
        await self.repository.create(
            _hash_code(code),
            project_id,
            label.strip() or f"wordpress {site_host}",
            site_host,
            approved_by,
            datetime.now(UTC) + timedelta(minutes=CODE_TTL_MINUTES),
        )
        return code

    async def exchange(self, code: str) -> tuple[ApiKey, str, str]:
        """Single use: trades the code for a new send-scoped key. Same error for an
        unknown, used or expired code, so nothing leaks about which one it was."""
        invalid = UnauthorizedError("Invalid or expired code")
        pairing = await self.repository.get_by_hash(_hash_code(code))
        if pairing is None or pairing.used_at is not None:
            raise invalid
        expires_at = pairing.expires_at
        if expires_at.tzinfo is None:  # SQLite in tests drops tzinfo
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < datetime.now(UTC):
            raise invalid
        if not await self.repository.consume(pairing.id):
            raise invalid
        api_key, plaintext = await self.api_key_service.create(
            pairing.project_id, pairing.label, scopes=[SCOPE_SEND]
        )
        project = await self.project_repository.get(pairing.project_id)
        return api_key, plaintext, project.slug if project else ""
