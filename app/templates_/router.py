import uuid

from fastapi import APIRouter, status

from app.api_keys.dependencies import CurrentApiKey
from app.api_keys.models import ApiKey
from app.core.exceptions import ForbiddenError
from app.templates_.dependencies import TemplateServiceDep
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
    project_id: uuid.UUID, current_api_key: CurrentApiKey, service: TemplateServiceDep
) -> list[TemplateRead]:
    _require_same_project(current_api_key, project_id)
    return await service.list_for_project(project_id)


@router.post(
    "/",
    response_model=TemplateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Publish a new template version",
)
async def publish_template(
    payload: TemplateCreate, current_api_key: CurrentApiKey, service: TemplateServiceDep
) -> TemplateRead:
    _require_same_project(current_api_key, payload.project_id)
    return await service.publish(payload)


@router.get("/{template_id}", response_model=TemplateRead, summary="Get a template")
async def get_template(
    template_id: uuid.UUID, current_api_key: CurrentApiKey, service: TemplateServiceDep
) -> TemplateRead:
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
    current_api_key: CurrentApiKey,
    service: TemplateServiceDep,
) -> TemplatePreviewResponse:
    template = await service.get(template_id)
    _require_same_project(current_api_key, template.project_id)
    subject, html = service.render(template, payload.payload)
    return TemplatePreviewResponse(subject=subject, html=html)
