import uuid
from typing import Any

import jsonschema
from jinja2.sandbox import SandboxedEnvironment

from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.templates_.models import Template
from app.templates_.repository import TemplateRepository
from app.templates_.schemas import TemplateCreate

# Template bodies are admin/user-authored content, not trusted application code,
# and payload data from producer apps flows into them -- sandboxed to block
# access to unsafe attributes/globals.
_jinja_env = SandboxedEnvironment(autoescape=True)


@log_errors
class TemplateService:
    def __init__(self, repository: TemplateRepository):
        self.repository = repository

    async def list_for_project(self, project_id: uuid.UUID) -> list[Template]:
        return await self.repository.list_for_project(project_id)

    async def get(self, template_id: uuid.UUID) -> Template:
        template = await self.repository.get(template_id)
        if template is None:
            raise NotFoundError(f"Template {template_id} not found")
        return template

    async def get_active(self, project_id: uuid.UUID, key: str) -> Template:
        template = await self.repository.get_active(project_id, key)
        if template is None:
            raise NotFoundError(f"No active template {key!r} for project {project_id}")
        return template

    async def publish(self, payload: TemplateCreate) -> Template:
        """Creates a new version of a template, deactivating the prior active version."""
        latest = await self.repository.get_latest_version(payload.project_id, payload.key)
        next_version = (latest.version + 1) if latest else 1

        current_active = await self.repository.get_active(payload.project_id, payload.key)
        if current_active is not None:
            await self.repository.deactivate(current_active)

        template = Template(
            project_id=payload.project_id,
            key=payload.key,
            subject_template=payload.subject_template,
            html_template=payload.html_template,
            variables_schema=payload.variables_schema,
            version=next_version,
            is_active=True,
        )
        return await self.repository.create(template)

    def render(self, template: Template, payload: dict[str, Any]) -> tuple[str, str]:
        if template.variables_schema:
            try:
                jsonschema.validate(instance=payload, schema=template.variables_schema)
            except jsonschema.ValidationError as exc:
                raise ValidationFailedError(
                    f"Payload does not match template schema: {exc.message}"
                ) from exc

        subject = _jinja_env.from_string(template.subject_template).render(**payload)
        html = _jinja_env.from_string(template.html_template).render(**payload)
        return subject, html
