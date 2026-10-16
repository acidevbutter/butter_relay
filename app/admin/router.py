"""Thin HTML controller layer: parses/validates via FastAPI + Pydantic forms,
calls into each domain's own service, renders a Jinja2 template. No business
logic lives here -- it's a view over projects/api_keys/templates_/messages.

Form errors (validation, conflicts, provider failures) never escape as JSON/500:
every POST re-renders its page with a readable message and the submitted values.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.admin.templating import templates
from app.api_keys.dependencies import ApiKeyServiceDep
from app.auth.dependencies import CurrentAdminUser
from app.auth.models import AdminUser
from app.core.email_provider import (
    DeliveryProvider,
    EmailSendError,
    OutboundEmail,
    SmtpSecurity,
)
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.core.queue_stats import queue_backlog
from app.core.secrets import SecretsKeyError
from app.email_types.dependencies import EmailTypeServiceDep
from app.email_types.schemas import EmailCategory, EmailTypeCreate, EmailTypeMode
from app.messages.dependencies import MessageServiceDep
from app.messages.models import MessageStatus
from app.projects.dependencies import ProjectServiceDep
from app.projects.schemas import (
    DeliveryConfig,
    ProjectCreate,
    ProjectSecretsUpdate,
    ProjectUpdate,
)
from app.rejected_messages.dependencies import RejectedMessageServiceDep
from app.settings.config import settings
from app.templates_.dependencies import TemplateServiceDep
from app.templates_.schemas import TemplateCreate

router = APIRouter(prefix="/admin", tags=["admin"])

_EMAIL = TypeAdapter(EmailStr)


def _format_validation_error(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or 'value'}: {err['msg']}"
        for err in exc.errors()
    )


def _delivery_config(
    smtp_host: str, smtp_port: str, smtp_security: str = "", smtp_username: str = ""
) -> DeliveryConfig:
    """Blank form fields mean "use the deployment default" (SMTP_HOST / SMTP_PORT)."""
    return DeliveryConfig(
        smtp_host=smtp_host.strip() or None,
        smtp_port=int(smtp_port) if smtp_port.strip() else None,
        smtp_security=SmtpSecurity(smtp_security) if smtp_security.strip() else None,
        smtp_username=smtp_username.strip() or None,
    )


@router.get("", summary="Admin dashboard home")
async def dashboard(
    request: Request,
    admin_user: CurrentAdminUser,
    message_service: MessageServiceDep,
    project_service: ProjectServiceDep,
    rejected_message_service: RejectedMessageServiceDep,
) -> HTMLResponse:
    recent_messages = await message_service.list(limit=10)
    # Minimum delivery health (DVB-188): last 24h by status, terminal failures,
    # rejected tasks, rows stuck mid-send and the Celery backlog per queue.
    stats = await message_service.stats()
    stats["rejected"] = await rejected_message_service.count_recent()
    queues = sorted(
        {settings.relay_queue_name}
        | {p.queue_name for p in await project_service.list() if p.is_active}
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "admin_user": admin_user,
            "recent_messages": recent_messages,
            "stats": stats,
            "backlog": await queue_backlog(queues),
        },
    )


# --- Projects -------------------------------------------------------------


def _projects_page(
    request: Request,
    admin_user: AdminUser,
    projects: list[Any],
    *,
    error: str | None = None,
    form: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "projects.html",
        {
            "admin_user": admin_user,
            "projects": projects,
            "error": error,
            "form": form or {},
            "providers": list(DeliveryProvider),
        },
        status_code=status_code,
    )


@router.get("/projects", summary="List + create projects")
async def list_projects(
    request: Request, admin_user: CurrentAdminUser, service: ProjectServiceDep
) -> HTMLResponse:
    return _projects_page(request, admin_user, await service.list())


@router.post("/projects", summary="Create a project")
async def create_project(
    request: Request,
    admin_user: CurrentAdminUser,
    service: ProjectServiceDep,
    name: Annotated[str, Form()],
    slug: Annotated[str, Form()],
    default_sender_email: Annotated[str, Form()],
    default_sender_name: Annotated[str, Form()],
    delivery_provider: Annotated[str, Form()] = DeliveryProvider.BREVO.value,
    smtp_host: Annotated[str, Form()] = "",
    smtp_port: Annotated[str, Form()] = "",
    smtp_security: Annotated[str, Form()] = "",
    smtp_username: Annotated[str, Form()] = "",
    external: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    form = {
        "name": name,
        "slug": slug,
        "default_sender_email": default_sender_email,
        "default_sender_name": default_sender_name,
        "delivery_provider": delivery_provider,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_security": smtp_security,
        "smtp_username": smtp_username,
        "external": external,
    }
    try:
        payload = ProjectCreate(
            name=name,
            slug=slug,
            external=external,
            default_sender_email=default_sender_email,
            default_sender_name=default_sender_name,
            delivery_provider=DeliveryProvider(delivery_provider),
            delivery_config=_delivery_config(smtp_host, smtp_port, smtp_security, smtp_username),
        )
        await service.create(payload)
    except ValidationError as exc:
        return _projects_page(
            request, admin_user, await service.list(),
            error=_format_validation_error(exc), form=form, status_code=422,
        )
    except ValueError as exc:  # unknown provider / non-numeric port
        return _projects_page(
            request, admin_user, await service.list(), error=str(exc), form=form, status_code=422
        )
    except ConflictError as exc:
        return _projects_page(
            request, admin_user, await service.list(), error=exc.detail, form=form, status_code=409
        )
    return _projects_page(request, admin_user, await service.list())


@dataclass
class ProjectPage:
    """Everything the project detail page needs, so each POST handler can
    re-render it (with a notice or an error) in one call."""

    project_service: ProjectServiceDep
    api_key_service: ApiKeyServiceDep
    template_service: TemplateServiceDep
    email_type_service: EmailTypeServiceDep

    async def render(
        self,
        request: Request,
        admin_user: AdminUser,
        project_id: uuid.UUID,
        *,
        notice: str | None = None,
        error: str | None = None,
        new_key_plaintext: str | None = None,
        form: dict[str, Any] | None = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        project = await self.project_service.get(project_id)
        return templates.TemplateResponse(
            request,
            "project_detail.html",
            {
                "admin_user": admin_user,
                "project": project,
                "api_keys": await self.api_key_service.list_for_project(project_id),
                "templates": await self.template_service.list_for_project(project_id),
                "email_types": await self.email_type_service.list(project_id),
                "email_categories": list(EmailCategory),
                "email_modes": list(EmailTypeMode),
                "new_key_plaintext": new_key_plaintext,
                "notice": notice,
                "error": error,
                "form": form or {},
                "providers": list(DeliveryProvider),
                "default_smtp": f"{settings.smtp_host}:{settings.smtp_port}",
            },
            status_code=status_code,
        )


ProjectPageDep = Annotated[ProjectPage, Depends()]


@router.get("/projects/{project_id}", summary="Project detail: settings, API keys, templates")
async def project_detail(
    request: Request, admin_user: CurrentAdminUser, project_id: uuid.UUID, page: ProjectPageDep
) -> HTMLResponse:
    return await page.render(request, admin_user, project_id)


@router.post("/projects/{project_id}/settings", summary="Update project + delivery settings")
async def update_project_settings(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    page: ProjectPageDep,
    name: Annotated[str, Form()],
    default_sender_email: Annotated[str, Form()],
    default_sender_name: Annotated[str, Form()],
    delivery_provider: Annotated[str, Form()],
    smtp_host: Annotated[str, Form()] = "",
    smtp_port: Annotated[str, Form()] = "",
    smtp_security: Annotated[str, Form()] = "",
    smtp_username: Annotated[str, Form()] = "",
    external: Annotated[bool, Form()] = False,
    is_active: Annotated[bool, Form()] = False,
    allow_undeclared_types: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    try:
        payload = ProjectUpdate(
            name=name,
            default_sender_email=default_sender_email,
            default_sender_name=default_sender_name,
            delivery_provider=DeliveryProvider(delivery_provider),
            delivery_config=_delivery_config(smtp_host, smtp_port, smtp_security, smtp_username),
            external=external,
            is_active=is_active,
            allow_undeclared_types=allow_undeclared_types,
        )
        await page.project_service.update(project_id, payload)
    except ValidationError as exc:
        return await page.render(
            request, admin_user, project_id,
            error=_format_validation_error(exc), status_code=422,
        )
    except ValueError as exc:
        return await page.render(request, admin_user, project_id, error=str(exc), status_code=422)
    except ConflictError as exc:
        return await page.render(request, admin_user, project_id, error=exc.detail, status_code=409)
    return await page.render(request, admin_user, project_id, notice="Settings saved.")


@router.post("/projects/{project_id}/secrets", summary="Set write-only delivery secrets")
async def update_project_secrets(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    page: ProjectPageDep,
    smtp_password: Annotated[str, Form()] = "",
    brevo_api_key: Annotated[str, Form()] = "",
    clear: Annotated[list[str] | None, Form()] = None,
) -> HTMLResponse:
    try:
        payload = ProjectSecretsUpdate(
            smtp_password=smtp_password or None,
            brevo_api_key=brevo_api_key or None,
            clear=clear or [],
        )
        await page.project_service.update_secrets(project_id, payload)
    except ValidationError as exc:
        return await page.render(
            request, admin_user, project_id,
            error=_format_validation_error(exc), status_code=422,
        )
    except (ValidationFailedError, SecretsKeyError) as exc:
        detail = exc.detail if isinstance(exc, ValidationFailedError) else str(exc)
        return await page.render(request, admin_user, project_id, error=detail, status_code=422)
    return await page.render(request, admin_user, project_id, notice="Secrets saved.")


@router.post("/projects/{project_id}/test-send", summary="Send a test e-mail through the project")
async def send_test_email(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    page: ProjectPageDep,
    to: Annotated[str, Form()],
) -> HTMLResponse:
    """Uses the project's own delivery provider directly (no queue, no template),
    so an admin can verify a channel right after choosing it."""
    try:
        recipient = str(_EMAIL.validate_python(to))
    except ValidationError:
        return await page.render(
            request, admin_user, project_id, error=f"{to!r} is not a valid e-mail.", status_code=422
        )
    project = await page.project_service.get(project_id)
    if not settings.is_recipient_allowed(recipient):
        return await page.render(
            request, admin_user, project_id,
            error="Recipient blocked by the non-production delivery policy.", status_code=422,
        )
    try:
        provider = page.project_service.provider_for(project)
        result = await provider.send(
            OutboundEmail(
                to=[recipient],
                subject=f"Butter Relay test - {project.name}",
                html=(
                    f"<p>This is a test e-mail from <strong>{project.name}</strong> sent "
                    f"through the <code>{project.delivery_provider}</code> delivery channel.</p>"
                ),
                from_email=project.default_sender_email,
                from_name=project.default_sender_name,
            )
        )
    except (EmailSendError, SecretsKeyError) as exc:
        detail = exc.detail if isinstance(exc, EmailSendError) else str(exc)
        return await page.render(
            request, admin_user, project_id,
            error=f"Test send failed: {detail}", status_code=502,
        )
    return await page.render(
        request, admin_user, project_id,
        notice=(
            f"Test e-mail sent to {recipient} via {project.delivery_provider} "
            f"(id {result.provider_message_id})."
        ),
    )


@router.post("/projects/{project_id}/api-keys", summary="Generate an API key")
async def create_api_key(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    label: Annotated[str, Form()],
    page: ProjectPageDep,
) -> HTMLResponse:
    _, plaintext_key = await page.api_key_service.create(project_id, label)
    return await page.render(request, admin_user, project_id, new_key_plaintext=plaintext_key)


@router.post("/projects/{project_id}/api-keys/{api_key_id}/revoke", summary="Revoke an API key")
async def revoke_api_key(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    api_key_id: uuid.UUID,
    page: ProjectPageDep,
) -> HTMLResponse:
    try:
        await page.api_key_service.revoke(api_key_id, project_id)
    except NotFoundError as exc:
        return await page.render(request, admin_user, project_id, error=exc.detail, status_code=404)
    return await page.render(request, admin_user, project_id, notice="API key revoked.")


# --- Templates --------------------------------------------------------------


@router.post("/projects/{project_id}/templates", summary="Publish a new template version")
async def publish_template(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    key: Annotated[str, Form()],
    subject_template: Annotated[str, Form()],
    page: ProjectPageDep,
    html_template: Annotated[str, Form()] = "",
    variables_schema: Annotated[str, Form()] = "",
    html_file: Annotated[UploadFile | None, File()] = None,
) -> HTMLResponse:
    if html_file is not None and html_file.filename:
        # An uploaded .html file wins over the textarea (DVB-307); same 512 KB cap.
        raw = await html_file.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            return await page.render(
                request, admin_user, project_id,
                error="HTML file is larger than 512 KB", status_code=422,
            )
        try:
            html_template = raw.decode("utf-8")
        except UnicodeDecodeError:
            return await page.render(
                request, admin_user, project_id,
                error="HTML file must be UTF-8 text", status_code=422,
            )
    form = {
        "key": key,
        "subject_template": subject_template,
        "html_template": html_template,
        "variables_schema": variables_schema,
    }
    try:
        schema = json.loads(variables_schema) if variables_schema.strip() else {}
        if not isinstance(schema, dict):
            raise ValueError("variables_schema must be a JSON object")
        created = await page.template_service.publish(
            TemplateCreate(
                project_id=project_id,
                key=key,
                subject_template=subject_template,
                html_template=html_template,
                variables_schema=schema,
            )
        )
    except json.JSONDecodeError as exc:
        return await page.render(
            request, admin_user, project_id,
            error=f"variables_schema is not valid JSON: {exc}", form=form, status_code=422,
        )
    except ValidationError as exc:
        return await page.render(
            request, admin_user, project_id,
            error=_format_validation_error(exc), form=form, status_code=422,
        )
    except ValueError as exc:
        return await page.render(
            request, admin_user, project_id, error=str(exc), form=form, status_code=422
        )
    except (ValidationFailedError, ConflictError) as exc:
        return await page.render(
            request, admin_user, project_id, error=exc.detail, form=form, status_code=422
        )
    return await page.render(
        request, admin_user, project_id,
        notice=f"Published {created.key!r} v{created.version} (now active).",
    )


# --- E-mail types (DVB-307) ---------------------------------------------------


@router.post("/projects/{project_id}/email-types", summary="Declare or update an e-mail type")
async def save_email_type(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    page: ProjectPageDep,
    key: Annotated[str, Form()],
    category: Annotated[str, Form()] = EmailCategory.TRANSACTIONAL.value,
    mode: Annotated[str, Form()] = EmailTypeMode.TEMPLATE.value,
    description: Annotated[str, Form()] = "",
    locale: Annotated[str, Form()] = "",
    sample_payload: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        sample = json.loads(sample_payload) if sample_payload.strip() else {}
        if not isinstance(sample, dict):
            raise ValueError("sample_payload must be a JSON object")
        payload = EmailTypeCreate(
            key=key.strip(),
            category=EmailCategory(category),
            mode=EmailTypeMode(mode),
            description=description.strip() or None,
            locale=locale.strip() or None,
            sample_payload=sample,
        )
        email_type, outcome = await page.email_type_service.upsert(project_id, payload)
    except json.JSONDecodeError as exc:
        return await page.render(
            request, admin_user, project_id,
            error=f"sample_payload is not valid JSON: {exc}", status_code=422,
        )
    except ValidationError as exc:
        return await page.render(
            request, admin_user, project_id,
            error=_format_validation_error(exc), status_code=422,
        )
    except ValueError as exc:
        return await page.render(request, admin_user, project_id, error=str(exc), status_code=422)
    return await page.render(
        request, admin_user, project_id, notice=f"E-mail type {email_type.key!r} {outcome}."
    )


@router.post(
    "/projects/{project_id}/email-types/{email_type_id}/toggle",
    summary="Activate or deactivate an e-mail type",
)
async def toggle_email_type(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    email_type_id: uuid.UUID,
    page: ProjectPageDep,
) -> HTMLResponse:
    current = await page.email_type_service.get(project_id, email_type_id)
    updated = await page.email_type_service.set_active(
        project_id, email_type_id, not current.is_active
    )
    state = "activated" if updated.is_active else "deactivated"
    return await page.render(
        request, admin_user, project_id, notice=f"E-mail type {updated.key!r} {state}."
    )


@router.post(
    "/projects/{project_id}/email-types/{email_type_id}/preview",
    summary="Render a template e-mail type with its sample payload",
)
async def preview_email_type(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    email_type_id: uuid.UUID,
    page: ProjectPageDep,
) -> HTMLResponse:
    email_type = await page.email_type_service.get(project_id, email_type_id)
    if email_type.mode == EmailTypeMode.RAW.value:
        return templates.TemplateResponse(
            request, "partials/_preview.html",
            {"error": "Raw types are sent as the client provides them; nothing to render."},
        )
    try:
        template = await page.template_service.get_active(project_id, email_type.key)
        subject, html = page.template_service.render(template, email_type.sample_payload)
    except (NotFoundError, ValidationFailedError) as exc:
        return templates.TemplateResponse(
            request, "partials/_preview.html", {"error": exc.detail}
        )
    return templates.TemplateResponse(
        request, "partials/_preview.html", {"subject": subject, "html": html, "error": None}
    )


@router.post("/templates/{template_id}/activate", summary="Make a template version active")
async def activate_template(
    request: Request,
    admin_user: CurrentAdminUser,
    template_id: uuid.UUID,
    page: ProjectPageDep,
) -> HTMLResponse:
    template = await page.template_service.activate(template_id)
    return await page.render(
        request, admin_user, template.project_id,
        notice=f"{template.key!r} v{template.version} is now the active version.",
    )


@router.post("/templates/{template_id}/preview", summary="Render a template preview (HTMX partial)")
async def preview_template(
    request: Request,
    admin_user: CurrentAdminUser,
    template_id: uuid.UUID,
    sample_payload: Annotated[str, Form()],
    service: TemplateServiceDep,
) -> HTMLResponse:
    template = await service.get(template_id)
    try:
        payload = json.loads(sample_payload) if sample_payload.strip() else {}
    except json.JSONDecodeError as exc:
        return templates.TemplateResponse(
            request, "partials/_preview.html", {"error": f"Invalid JSON payload: {exc}"}
        )
    try:
        subject, html = service.render(template, payload)
    except ValidationFailedError as exc:
        return templates.TemplateResponse(request, "partials/_preview.html", {"error": exc.detail})
    return templates.TemplateResponse(
        request, "partials/_preview.html", {"subject": subject, "html": html, "error": None}
    )


# --- Messages (the email log) ------------------------------------------------


@router.get("/messages", summary="Message log with filters")
async def list_messages(
    request: Request,
    admin_user: CurrentAdminUser,
    message_service: MessageServiceDep,
    project_service: ProjectServiceDep,
    project_id: uuid.UUID | None = None,
    status_filter: MessageStatus | None = None,
) -> HTMLResponse:
    messages = await message_service.list(project_id, status_filter, limit=200)
    projects = await project_service.list()
    return templates.TemplateResponse(
        request,
        "messages.html",
        {
            "admin_user": admin_user,
            "messages": messages,
            "projects": projects,
            "project_id": project_id,
            "status_filter": status_filter,
        },
    )


@router.get("/rejected-messages", summary="Tasks the worker could not parse (DVB-456)")
async def list_rejected_messages(
    request: Request,
    admin_user: CurrentAdminUser,
    rejected_message_service: RejectedMessageServiceDep,
) -> HTMLResponse:
    rejected = await rejected_message_service.list(limit=200)
    return templates.TemplateResponse(
        request,
        "rejected_messages.html",
        {"admin_user": admin_user, "rejected": rejected},
    )


@router.get("/messages/{message_id}", summary="View a message's rendered email")
async def message_detail(
    request: Request,
    admin_user: CurrentAdminUser,
    message_id: uuid.UUID,
    message_service: MessageServiceDep,
    template_service: TemplateServiceDep,
) -> HTMLResponse:
    message = await message_service.get(message_id)
    subject, html, render_error = message.rendered_subject or "", None, None
    if message.template_id is not None:
        template = await template_service.get(message.template_id)
        try:
            subject, html = template_service.render(template, message.payload)
        except ValidationFailedError as exc:
            render_error = str(exc)
    return templates.TemplateResponse(
        request,
        "message_detail.html",
        {
            "admin_user": admin_user,
            "message": message,
            "attempts": await message_service.list_attempts(message.id),
            "subject": subject,
            "html": html,
            "render_error": render_error,
        },
    )


@router.post("/messages/{message_id}/resend", summary="Manually resend a failed message")
async def resend_message(
    request: Request,
    admin_user: CurrentAdminUser,
    message_id: uuid.UUID,
    message_service: MessageServiceDep,
    project_service: ProjectServiceDep,
    template_service: TemplateServiceDep,
) -> HTMLResponse:
    message = await message_service.get(message_id)
    project = await project_service.get(message.project_id)

    try:
        if message.template_id is not None:
            template = await template_service.get(message.template_id)
            subject, html = template_service.render(template, message.payload)
        else:
            subject, html = message.rendered_subject or "", ""
        message = await message_service.mark_processing(message, subject)
        if not settings.is_recipient_allowed(message.to_email):
            raise EmailSendError("recipient blocked by non-production delivery policy")
        provider = project_service.provider_for(project)
        result = await provider.send(
            OutboundEmail(
                to=[message.to_email],
                subject=subject,
                html=html,
                from_email=project.default_sender_email,
                from_name=project.default_sender_name,
            )
        )
    except (EmailSendError, ValidationFailedError) as exc:
        await message_service.mark_failed(message, exc.detail)
    else:
        await message_service.mark_sent(message, result.provider_message_id)

    messages = await message_service.list(limit=200)
    projects = await project_service.list()
    return templates.TemplateResponse(
        request,
        "messages.html",
        {
            "admin_user": admin_user,
            "messages": messages,
            "projects": projects,
            "project_id": None,
            "status_filter": None,
        },
    )
