"""Thin HTML controller layer: parses/validates via FastAPI + Pydantic forms,
calls into each domain's own service, renders a Jinja2 template. No business
logic lives here -- it's a view over projects/api_keys/templates_/messages.
"""

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.admin.templating import templates
from app.api_keys.dependencies import ApiKeyServiceDep
from app.auth.dependencies import CurrentAdminUser
from app.core.email_provider import EmailSendError, get_email_provider
from app.core.exceptions import ValidationFailedError
from app.messages.dependencies import MessageServiceDep
from app.messages.models import MessageStatus
from app.projects.dependencies import ProjectServiceDep
from app.projects.schemas import ProjectCreate
from app.settings.config import settings
from app.templates_.dependencies import TemplateServiceDep
from app.templates_.schemas import TemplateCreate

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("", summary="Admin dashboard home")
async def dashboard(
    request: Request, admin_user: CurrentAdminUser, message_service: MessageServiceDep
) -> HTMLResponse:
    recent_messages = await message_service.list(limit=10)
    return templates.TemplateResponse(
        request, "dashboard.html", {"admin_user": admin_user, "recent_messages": recent_messages}
    )


# --- Projects -------------------------------------------------------------


@router.get("/projects", summary="List + create projects")
async def list_projects(
    request: Request, admin_user: CurrentAdminUser, service: ProjectServiceDep
) -> HTMLResponse:
    projects = await service.list()
    return templates.TemplateResponse(
        request, "projects.html", {"admin_user": admin_user, "projects": projects, "error": None}
    )


@router.post("/projects", summary="Create a project")
async def create_project(
    request: Request,
    admin_user: CurrentAdminUser,
    service: ProjectServiceDep,
    name: Annotated[str, Form()],
    slug: Annotated[str, Form()],
    default_sender_email: Annotated[str, Form()],
    default_sender_name: Annotated[str, Form()],
    external: Annotated[bool, Form()] = False,
) -> HTMLResponse:
    await service.create(
        ProjectCreate(
            name=name,
            slug=slug,
            external=external,
            default_sender_email=default_sender_email,
            default_sender_name=default_sender_name,
        )
    )
    projects = await service.list()
    return templates.TemplateResponse(
        request, "projects.html", {"admin_user": admin_user, "projects": projects, "error": None}
    )


@router.get("/projects/{project_id}", summary="Project detail: API keys + templates")
async def project_detail(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    project_service: ProjectServiceDep,
    api_key_service: ApiKeyServiceDep,
    template_service: TemplateServiceDep,
) -> HTMLResponse:
    project = await project_service.get(project_id)
    api_keys = await api_key_service.list_for_project(project_id)
    project_templates = await template_service.list_for_project(project_id)
    return templates.TemplateResponse(
        request,
        "project_detail.html",
        {
            "admin_user": admin_user,
            "project": project,
            "api_keys": api_keys,
            "templates": project_templates,
            "new_key_plaintext": None,
        },
    )


@router.post("/projects/{project_id}/api-keys", summary="Generate an API key")
async def create_api_key(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    label: Annotated[str, Form()],
    project_service: ProjectServiceDep,
    api_key_service: ApiKeyServiceDep,
    template_service: TemplateServiceDep,
) -> HTMLResponse:
    api_key, plaintext_key = await api_key_service.create(project_id, label)
    project = await project_service.get(project_id)
    api_keys = await api_key_service.list_for_project(project_id)
    project_templates = await template_service.list_for_project(project_id)
    return templates.TemplateResponse(
        request,
        "project_detail.html",
        {
            "admin_user": admin_user,
            "project": project,
            "api_keys": api_keys,
            "templates": project_templates,
            "new_key_plaintext": plaintext_key,
        },
    )


@router.post("/projects/{project_id}/api-keys/{api_key_id}/revoke", summary="Revoke an API key")
async def revoke_api_key(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    api_key_id: uuid.UUID,
    project_service: ProjectServiceDep,
    api_key_service: ApiKeyServiceDep,
    template_service: TemplateServiceDep,
) -> HTMLResponse:
    await api_key_service.revoke(api_key_id, project_id)
    project = await project_service.get(project_id)
    api_keys = await api_key_service.list_for_project(project_id)
    project_templates = await template_service.list_for_project(project_id)
    return templates.TemplateResponse(
        request,
        "project_detail.html",
        {
            "admin_user": admin_user,
            "project": project,
            "api_keys": api_keys,
            "templates": project_templates,
            "new_key_plaintext": None,
        },
    )


# --- Templates --------------------------------------------------------------


@router.post("/projects/{project_id}/templates", summary="Publish a new template version")
async def publish_template(
    request: Request,
    admin_user: CurrentAdminUser,
    project_id: uuid.UUID,
    key: Annotated[str, Form()],
    subject_template: Annotated[str, Form()],
    html_template: Annotated[str, Form()],
    project_service: ProjectServiceDep,
    api_key_service: ApiKeyServiceDep,
    template_service: TemplateServiceDep,
) -> HTMLResponse:
    await template_service.publish(
        TemplateCreate(
            project_id=project_id,
            key=key,
            subject_template=subject_template,
            html_template=html_template,
        )
    )
    project = await project_service.get(project_id)
    api_keys = await api_key_service.list_for_project(project_id)
    project_templates = await template_service.list_for_project(project_id)
    return templates.TemplateResponse(
        request,
        "project_detail.html",
        {
            "admin_user": admin_user,
            "project": project,
            "api_keys": api_keys,
            "templates": project_templates,
            "new_key_plaintext": None,
        },
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
    subject, html = service.render(template, payload)
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

    if message.template_id is not None:
        template = await template_service.get(message.template_id)
        subject, html = template_service.render(template, message.payload)
    else:
        subject, html = message.rendered_subject or "", ""

    message = await message_service.mark_processing(message, subject)
    provider = get_email_provider(settings)
    try:
        result = await provider.send(
            to=message.to_email,
            subject=subject,
            html=html,
            from_email=project.default_sender_email,
            from_name=project.default_sender_name,
        )
    except EmailSendError as exc:
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
