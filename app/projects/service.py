import builtins
import uuid
from typing import Any

from app.core.decorators import log_errors
from app.core.email_provider import (
    DeliveryProvider,
    EmailProvider,
    get_email_provider_for_project,
)
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.core.secrets import SECRET_FIELDS, decrypt_secrets, encrypt_secrets
from app.projects.models import Project
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate, ProjectSecretsUpdate, ProjectUpdate
from app.settings.config import settings
from app.worker.queues import add_consumer_for_project


@log_errors
class ProjectService:
    def __init__(self, repository: ProjectRepository):
        self.repository = repository

    async def list(self) -> list[Project]:
        return await self.repository.list()

    async def get(self, project_id: uuid.UUID) -> Project:
        project = await self.repository.get(project_id)
        if project is None:
            raise NotFoundError(f"Project {project_id} not found")
        return project

    async def get_by_slug(self, slug: str) -> Project:
        project = await self.repository.get_by_slug(slug)
        if project is None:
            raise NotFoundError(f"Project with slug {slug!r} not found")
        return project

    async def create(self, payload: ProjectCreate) -> Project:
        project = await self.repository.create(payload)
        add_consumer_for_project(project)
        return project

    async def update(self, project_id: uuid.UUID, payload: ProjectUpdate) -> Project:
        project = await self.get(project_id)
        return await self.repository.update(project, payload)

    async def update_secrets(self, project_id: uuid.UUID, payload: ProjectSecretsUpdate) -> Project:
        """Merges write-only secrets into the encrypted blob (DVB-306)."""
        unknown = set(payload.clear) - set(SECRET_FIELDS)
        if unknown:
            raise ValidationFailedError(f"Unknown secret(s) to clear: {sorted(unknown)}")
        project = await self.get(project_id)
        current = decrypt_secrets(project.delivery_secrets_enc)
        for name in SECRET_FIELDS:
            value = getattr(payload, name)
            if value:
                current[name] = value
        for name in payload.clear:
            current.pop(name, None)
        return await self.repository.set_secrets(project, encrypt_secrets(current))

    def delivery_secrets(self, project: Project) -> dict[str, Any]:
        return decrypt_secrets(project.delivery_secrets_enc)

    def provider_for(self, project: Project) -> EmailProvider:
        """The project's delivery channel with its own (decrypted) credentials."""
        return get_email_provider_for_project(
            project.delivery_provider,
            project.delivery_config,
            settings,
            self.delivery_secrets(project),
        )

    async def projects_needing_shared_brevo_key(self) -> builtins.list[Project]:
        """Active Brevo projects without their own key, i.e. relying on BREVO_API_KEY."""
        return [
            project
            for project in await self.list()
            if project.is_active
            and project.delivery_provider == DeliveryProvider.BREVO.value
            and not project.has_brevo_api_key
        ]
