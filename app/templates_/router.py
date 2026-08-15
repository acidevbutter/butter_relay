import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.dependencies import DbSession
from app.templates_.repository import TemplateRepository
from app.templates_.schemas import (
    TemplateCreate,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
    TemplateRead,
)
from app.templates_.service import TemplateService


def get_template_service(db: DbSession) -> TemplateService:
    return TemplateService(TemplateRepository(db))


TemplateServiceDep = Annotated[TemplateService, Depends(get_template_service)]

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


@router.get("/", response_model=list[TemplateRead], summary="List templates for a project")
async def list_templates(project_id: uuid.UUID, service: TemplateServiceDep) -> list[TemplateRead]:
    return await service.list_for_project(project_id)


@router.post(
    "/",
    response_model=TemplateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Publish a new template version",
)
async def publish_template(payload: TemplateCreate, service: TemplateServiceDep) -> TemplateRead:
    return await service.publish(payload)


@router.get("/{template_id}", response_model=TemplateRead, summary="Get a template")
async def get_template(template_id: uuid.UUID, service: TemplateServiceDep) -> TemplateRead:
    return await service.get(template_id)


@router.post(
    "/{template_id}/preview",
    response_model=TemplatePreviewResponse,
    summary="Render a template with sample payload",
)
async def preview_template(
    template_id: uuid.UUID, payload: TemplatePreviewRequest, service: TemplateServiceDep
) -> TemplatePreviewResponse:
    template = await service.get(template_id)
    subject, html = service.render(template, payload.payload)
    return TemplatePreviewResponse(subject=subject, html=html)
