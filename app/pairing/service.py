import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from app.api_keys.models import SCOPE_SEND, ApiKey
from app.api_keys.service import ApiKeyService
from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError, UnauthorizedError, ValidationFailedError
from app.pairing.models import ConnectRequest
from app.pairing.repository import PairingRepository
from app.pairing.schemas import ConnectRequestCreate
from app.projects.models import Project
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate
from app.projects.service import ProjectService

_CODE_PREFIX = "brp_"  # "butter relay pairing"
_REQUEST_PREFIX = "brq_"  # "butter relay request"
CODE_TTL_MINUTES = 10
REQUEST_TTL_MINUTES = 30
CALLBACK_PATH = "/wp-admin/admin-post.php"
CALLBACK_ACTION = "ediy_relay_connect_callback"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)  # SQLite drops tz


def suggested_slug(site_host: str) -> str:
    """"www.ediyporn.com" -> "ediyporn": the first label of the host, slug-safe."""
    label = site_host.lower().removeprefix("www.").split(".")[0]
    return re.sub(r"[^a-z0-9]+", "-", label).strip("-") or "site"


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
        project_service: ProjectService | None = None,
    ):
        self.repository = repository
        self.project_repository = project_repository
        self.api_key_service = api_key_service
        self.project_service = project_service or ProjectService(project_repository)

    async def create_request(self, payload: ConnectRequestCreate) -> str:
        """Stores what the site posted. Returns the request id the browser carries."""
        site_host = validate_return_url(payload.return_url)
        token = _REQUEST_PREFIX + secrets.token_urlsafe(32)
        await self.repository.create_request(
            ConnectRequest(
                token_hash=_hash_code(token),
                site_host=site_host,
                site_name=payload.site_name.strip(),
                site_url=payload.site_url.strip(),
                sender_email=str(payload.sender_email),
                sender_name=payload.sender_name.strip(),
                return_url=payload.return_url,
                state=payload.state,
                expires_at=datetime.now(UTC) + timedelta(minutes=REQUEST_TTL_MINUTES),
            )
        )
        return token

    async def open_request(self, token: str) -> ConnectRequest:
        """The pending request, or NotFoundError when unknown, already used or expired."""
        found = await self.repository.get_request_by_hash(_hash_code(token))
        expired = found is not None and _aware(found.expires_at) < datetime.now(UTC)
        if found is None or found.used_at is not None or expired:
            raise NotFoundError("Connection request not found or expired; click Connect again")
        return found

    async def approve_request(
        self,
        token: str,
        project_id: uuid.UUID | None,
        new_project: ProjectCreate | None,
        label: str,
        approved_by: str,
    ) -> tuple[str, ConnectRequest, Project]:
        """Approves a posted request, for an existing project or a new one built from
        the site's data. Returns (code, request, project)."""
        request = await self.open_request(token)
        if new_project is not None:
            project = await self.project_service.create(new_project)
        elif project_id is not None:
            found = await self.project_repository.get(project_id)
            if found is None or not found.is_active:
                raise NotFoundError(f"Project {project_id} not found")
            project = found
        else:
            raise ValidationFailedError("Pick a project or create one")
        if not await self.repository.consume_request(request.id):
            raise NotFoundError("Connection request already approved")
        code = await self.approve(project.id, label, request.site_host, approved_by)
        return code, request, project

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
        if _aware(pairing.expires_at) < datetime.now(UTC):
            raise invalid
        if not await self.repository.consume(pairing.id):
            raise invalid
        api_key, plaintext = await self.api_key_service.create(
            pairing.project_id, pairing.label, scopes=[SCOPE_SEND]
        )
        project = await self.project_repository.get(pairing.project_id)
        return api_key, plaintext, project.slug if project else ""
