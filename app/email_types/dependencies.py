from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSession
from app.email_types.repository import EmailTypeRepository
from app.email_types.service import EmailTypeService


def get_email_type_service(db: DbSession) -> EmailTypeService:
    return EmailTypeService(EmailTypeRepository(db))


EmailTypeServiceDep = Annotated[EmailTypeService, Depends(get_email_type_service)]
