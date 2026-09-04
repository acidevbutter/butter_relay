from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSession
from app.projects.repository import ProjectRepository
from app.projects.service import ProjectService


def get_project_service(db: DbSession) -> ProjectService:
    return ProjectService(ProjectRepository(db))


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]
