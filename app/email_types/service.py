import builtins
import uuid

from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.email_types.models import EmailType
from app.email_types.repository import EmailTypeRepository
from app.email_types.schemas import EmailTypeCreate, EmailTypeMode

UNKNOWN_EMAIL_TYPE = "unknown_email_type"
EMAIL_TYPE_MODE_MISMATCH = "email_type_mode_mismatch"


@log_errors
class EmailTypeService:
    def __init__(self, repository: EmailTypeRepository):
        self.repository = repository

    async def list(
        self, project_id: uuid.UUID, *, active_only: bool = False
    ) -> builtins.list[EmailType]:
        return await self.repository.list_for_project(project_id, active_only=active_only)

    async def get(self, project_id: uuid.UUID, email_type_id: uuid.UUID) -> EmailType:
        email_type = await self.repository.get(email_type_id)
        if email_type is None or email_type.project_id != project_id:
            raise NotFoundError(f"Email type {email_type_id} not found")
        return email_type

    async def upsert(
        self, project_id: uuid.UUID, payload: EmailTypeCreate
    ) -> tuple[EmailType, str]:
        """Creates the type or updates the fields that changed. Returns (type, outcome)
        with outcome one of created / updated / unchanged -- idempotent (S5 manifests)."""
        existing = await self.repository.get_by_key(project_id, payload.key)
        if existing is None:
            return await self.repository.create(project_id, payload), "created"
        wanted = payload.model_dump(mode="json")
        changes = {k: v for k, v in wanted.items() if getattr(existing, k) != v}
        if not changes:
            return existing, "unchanged"
        return await self.repository.update(existing, changes), "updated"

    async def set_active(
        self, project_id: uuid.UUID, email_type_id: uuid.UUID, active: bool
    ) -> EmailType:
        email_type = await self.get(project_id, email_type_id)
        return await self.repository.update(email_type, {"is_active": active})

    async def resolve_for_send(
        self,
        project_id: uuid.UUID,
        key: str,
        mode: EmailTypeMode,
        *,
        allow_undeclared: bool,
    ) -> EmailType | None:
        """The declared, active type a send must match (S4). Raises 422 with a stable
        code when the type is unknown/inactive (unless the project allows undeclared
        types, then None) or does not accept the requested mode."""
        email_type = await self.repository.get_by_key(project_id, key)
        if email_type is None or not email_type.is_active:
            if allow_undeclared:
                return None
            raise ValidationFailedError(
                f"Email type {key!r} is not declared for this client", code=UNKNOWN_EMAIL_TYPE
            )
        if email_type.mode not in (mode.value, EmailTypeMode.BOTH.value):
            raise ValidationFailedError(
                f"Email type {key!r} accepts mode {email_type.mode!r}, not {mode.value!r}",
                code=EMAIL_TYPE_MODE_MISMATCH,
            )
        return email_type
