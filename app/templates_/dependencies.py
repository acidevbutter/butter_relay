from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSession
from app.templates_.repository import TemplateRepository
from app.templates_.service import TemplateService


def get_template_service(db: DbSession) -> TemplateService:
    return TemplateService(TemplateRepository(db))


TemplateServiceDep = Annotated[TemplateService, Depends(get_template_service)]
