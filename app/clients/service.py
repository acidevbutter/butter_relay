import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.schemas import ClientManifest, ManifestApplyResult, Outcome
from app.core.decorators import log_errors
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.core.secrets import SECRET_FIELDS, decrypt_secrets, encrypt_secrets
from app.email_types.models import EmailType
from app.projects.models import Project
from app.templates_.models import Template
from app.templates_.schemas import TemplateCreate
from app.templates_.service import TemplateService, content_hash
from app.worker.queues import add_consumer_for_project

_PROJECT_FIELDS = (
    "name",
    "external",
    "default_sender_email",
    "default_sender_name",
    "delivery_provider",
    "delivery_config",
    "allow_undeclared_types",
    "rate_per_minute",
    "is_active",
)
_EMAIL_TYPE_FIELDS = ("description", "category", "mode", "locale", "sample_payload", "is_active")
_PLACEHOLDER_PROJECT = uuid.UUID(int=0)


def _project_values(manifest: ClientManifest) -> dict[str, Any]:
    values = manifest.model_dump(mode="json", include=set(_PROJECT_FIELDS))
    config = values["delivery_config"]
    values["delivery_config"] = {k: v for k, v in config.items() if v is not None}
    return values


@log_errors
class ManifestService:
    """Applies a client manifest in one transaction: all or nothing. With `dry_run` the
    same diff is computed and rolled back; with `prune`, e-mail types and templates
    missing from the manifest are deactivated (never deleted: messages reference them)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def apply(
        self, slug: str, manifest: ClientManifest, *, dry_run: bool = False, prune: bool = False
    ) -> ManifestApplyResult:
        self._validate(manifest)
        project = (
            await self.session.execute(select(Project).where(Project.slug == slug))
        ).scalar_one_or_none()
        try:
            result, created = await self._apply(slug, project, manifest, prune, dry_run)
        except BaseException:
            await self.session.rollback()
            raise
        if dry_run:
            await self.session.rollback()
        else:
            await self.session.commit()
            if created is not None and result.changed:
                add_consumer_for_project(created)
        return result

    @staticmethod
    def _validate(manifest: ClientManifest) -> None:
        errors: list[dict[str, Any]] = []
        for section, keys in (
            ("email_types", [t.key for t in manifest.email_types]),
            ("templates", [t.key for t in manifest.templates]),
        ):
            seen: set[str] = set()
            for index, key in enumerate(keys):
                if key in seen:
                    errors.append({"loc": [section, index, "key"], "msg": f"duplicate key {key!r}"})
                seen.add(key)
        for index, template in enumerate(manifest.templates):
            try:
                TemplateService._validate_definition(
                    TemplateCreate(project_id=_PLACEHOLDER_PROJECT, **template.model_dump())
                )
            except ValidationFailedError as exc:
                errors.append({"loc": ["templates", index], "msg": exc.detail})
        if errors:
            raise ValidationFailedError("Invalid manifest", code="invalid_manifest", errors=errors)

    async def _apply(
        self,
        slug: str,
        project: Project | None,
        manifest: ClientManifest,
        prune: bool,
        dry_run: bool,
    ) -> tuple[ManifestApplyResult, Project | None]:
        wanted = _project_values(manifest)
        created: Project | None = None
        changes: list[str] = []
        if project is None:
            project = created = Project(slug=slug, **wanted)
            self.session.add(project)
            await self.session.flush()
            state = "created"
        else:
            changes = [f for f, v in wanted.items() if getattr(project, f) != v]
            for field in changes:
                setattr(project, field, wanted[field])
            state = "updated" if changes else "unchanged"

        secrets_set = self._apply_secrets(project, manifest)
        email_types = await self._apply_email_types(project, manifest, prune)
        templates = await self._apply_templates(project, manifest, prune)
        await self.session.flush()
        result = ManifestApplyResult(
            dry_run=dry_run,
            project=state,
            project_changes=changes,
            secrets_set=secrets_set,
            email_types=email_types,
            templates=templates,
        )
        return result, created

    @staticmethod
    def _apply_secrets(project: Project, manifest: ClientManifest) -> list[str]:
        if manifest.secrets is None:
            return []
        current = decrypt_secrets(project.delivery_secrets_enc)
        changed: list[str] = []
        for name in SECRET_FIELDS:
            value = getattr(manifest.secrets, name)
            if value and current.get(name) != value:
                current[name] = value
                changed.append(name)
        if changed:
            project.delivery_secrets_enc = encrypt_secrets(current)
        return changed

    async def _apply_email_types(
        self, project: Project, manifest: ClientManifest, prune: bool
    ) -> Outcome:
        outcome = Outcome()
        existing = {
            t.key: t
            for t in (
                await self.session.execute(
                    select(EmailType).where(EmailType.project_id == project.id)
                )
            ).scalars()
        }
        for payload in manifest.email_types:
            values = payload.model_dump(mode="json")
            current = existing.get(payload.key)
            if current is None:
                self.session.add(EmailType(project_id=project.id, **values))
                outcome.created.append(payload.key)
                continue
            changes = {f: values[f] for f in _EMAIL_TYPE_FIELDS if getattr(current, f) != values[f]}
            for field, value in changes.items():
                setattr(current, field, value)
            (outcome.updated if changes else outcome.unchanged).append(payload.key)
        if prune:
            declared = {t.key for t in manifest.email_types}
            for key, current in existing.items():
                if key not in declared and current.is_active:
                    current.is_active = False
                    outcome.pruned.append(key)
        return outcome

    async def _apply_templates(
        self, project: Project, manifest: ClientManifest, prune: bool
    ) -> Outcome:
        outcome = Outcome()
        rows = (
            await self.session.execute(
                select(Template)
                .where(Template.project_id == project.id)
                .order_by(Template.key, Template.version)
            )
        ).scalars()
        by_key: dict[str, list[Template]] = {}
        for row in rows:
            by_key.setdefault(row.key, []).append(row)
        for payload in manifest.templates:
            versions = by_key.get(payload.key, [])
            latest = versions[-1] if versions else None
            new_hash = content_hash(
                payload.subject_template, payload.html_template, payload.variables_schema
            )
            if latest is not None and new_hash == content_hash(
                latest.subject_template, latest.html_template, latest.variables_schema
            ):
                if latest.is_active:
                    outcome.unchanged.append(payload.key)
                else:
                    for version in versions:
                        version.is_active = version is latest
                    outcome.updated.append(payload.key)
                continue
            for version in versions:
                version.is_active = False
            self.session.add(
                Template(
                    project_id=project.id,
                    key=payload.key,
                    subject_template=payload.subject_template,
                    html_template=payload.html_template,
                    variables_schema=payload.variables_schema,
                    version=(latest.version + 1) if latest else 1,
                    is_active=True,
                )
            )
            (outcome.updated if latest else outcome.created).append(payload.key)
        if prune:
            declared = {t.key for t in manifest.templates}
            for key, versions in by_key.items():
                if key not in declared and any(v.is_active for v in versions):
                    for version in versions:
                        version.is_active = False
                    outcome.pruned.append(key)
        return outcome

    async def export(self, project: Project) -> ClientManifest:
        """The project's declared state, round-trippable into PUT; secrets never included."""
        email_types = (
            await self.session.execute(
                select(EmailType).where(EmailType.project_id == project.id).order_by(EmailType.key)
            )
        ).scalars()
        templates = (
            await self.session.execute(
                select(Template)
                .where(Template.project_id == project.id, Template.is_active.is_(True))
                .order_by(Template.key)
            )
        ).scalars()
        return ClientManifest.model_validate(
            {
                **{f: getattr(project, f) for f in _PROJECT_FIELDS},
                "email_types": [
                    {"key": t.key, **{f: getattr(t, f) for f in _EMAIL_TYPE_FIELDS}}
                    for t in email_types
                ],
                "templates": [
                    {
                        "key": t.key,
                        "subject_template": t.subject_template,
                        "html_template": t.html_template,
                        "variables_schema": t.variables_schema,
                    }
                    for t in templates
                ],
            }
        )


def require_found(project: Project | None, slug: str) -> Project:
    if project is None:
        raise NotFoundError(f"Project with slug {slug!r} not found")
    return project
