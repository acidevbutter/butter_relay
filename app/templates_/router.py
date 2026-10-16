import json
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile, status

from app.api_keys.dependencies import ManageApiKey, ReadApiKey
from app.api_keys.models import ApiKey
from app.core.exceptions import ForbiddenError, ValidationFailedError
from app.templates_.dependencies import TemplateServiceDep
from app.templates_.models import Template
from app.templates_.schemas import (
    TemplateCreate,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
    TemplateRead,
)

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


def _require_same_project(current_api_key: ApiKey, project_id: uuid.UUID) -> None:
    """See docs/auditoria-seguranca-2026-08-29.md (item 1) / docs/prompts/autenticar-api-v1.md."""
    if current_api_key.project_id != project_id:
        raise ForbiddenError("API key does not belong to this project")


@router.get("/", response_model=list[TemplateRead], summary="List templates for a project")
async def list_templates(
    project_id: uuid.UUID, current_api_key: ReadApiKey, service: TemplateServiceDep
) -> list[Template]:
    _require_same_project(current_api_key, project_id)
    return await service.list_for_project(project_id)


@router.post(
    "/",
    response_model=TemplateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Publish a new template version",
)
async def publish_template(
    payload: TemplateCreate, current_api_key: ManageApiKey, service: TemplateServiceDep
) -> Template:
    _require_same_project(current_api_key, payload.project_id)
    return await service.publish(payload)


@router.get("/{template_id}", response_model=TemplateRead, summary="Get a template")
async def get_template(
    template_id: uuid.UUID, current_api_key: ReadApiKey, service: TemplateServiceDep
) -> Template:
    template = await service.get(template_id)
    _require_same_project(current_api_key, template.project_id)
    return template


@router.post(
    "/{template_id}/preview",
    response_model=TemplatePreviewResponse,
    summary="Render a template with sample payload",
)
async def preview_template(
    template_id: uuid.UUID,
    payload: TemplatePreviewRequest,
    current_api_key: ReadApiKey,
    service: TemplateServiceDep,
) -> TemplatePreviewResponse:
    template = await service.get(template_id)
    _require_same_project(current_api_key, template.project_id)
    subject, html = service.render(template, payload.payload)
    return TemplatePreviewResponse(subject=subject, html=html)


@router.post(
    "/{template_id}/activate",
    response_model=TemplateRead,
    summary="Make this version the active one (rollback)",
)
async def activate_template(
    template_id: uuid.UUID, current_api_key: ManageApiKey, service: TemplateServiceDep
) -> Template:
    template = await service.get(template_id)
    _require_same_project(current_api_key, template.project_id)
    return await service.activate(template_id)


MAX_UPLOAD_BYTES = 512 * 1024


@router.post(
    "/upload",
    response_model=TemplateRead,
    summary="Upload an HTML file as a new template version (no-op when unchanged)",
    responses={200: {"description": "Same content as the latest version: nothing created"}},
    status_code=status.HTTP_201_CREATED,
)
async def upload_template(
    response: Response,
    current_api_key: ManageApiKey,
    service: TemplateServiceDep,
    file: Annotated[UploadFile, File(description=".html file, up to 512 KB")],
    key: Annotated[str, Form()],
    subject_template: Annotated[str, Form()],
    variables_schema: Annotated[str, Form()] = "",
    activate: Annotated[bool, Form()] = True,
) -> Template:
    """The project comes from the API key. Validation is the same as publishing
    (sandboxed Jinja, JSON Schema, key format): a broken upload is a 422 and never
    becomes the active version (DVB-307)."""
    if not (file.filename or "").lower().endswith((".html", ".htm")):
        raise ValidationFailedError("file must be an .html document", code="invalid_file")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationFailedError("file is larger than 512 KB", code="file_too_large")
    try:
        html = raw.decode("utf-8")
        schema = json.loads(variables_schema) if variables_schema.strip() else {}
    except UnicodeDecodeError as exc:
        raise ValidationFailedError("file must be UTF-8 text", code="invalid_file") from exc
    except json.JSONDecodeError as exc:
        raise ValidationFailedError(f"variables_schema is not valid JSON: {exc.msg}") from exc
    if not isinstance(schema, dict):
        raise ValidationFailedError("variables_schema must be a JSON object")
    try:
        payload = TemplateCreate(
            project_id=current_api_key.project_id,
            key=key,
            subject_template=subject_template,
            html_template=html,
            variables_schema=schema,
        )
    except ValueError as exc:  # pydantic ValidationError (key format, sizes)
        raise ValidationFailedError(str(exc)) from exc
    template, created = await service.publish_if_changed(payload, activate=activate)
    if not created:
        response.status_code = status.HTTP_200_OK
    return template
