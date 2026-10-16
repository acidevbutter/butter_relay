from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSession
from app.email_types.repository import EmailTypeRepository
from app.email_types.service import EmailTypeService
from app.messages.repository import MessageRepository
from app.messages.service import MessageSendService, MessageService
from app.projects.repository import ProjectRepository
from app.projects.service import ProjectService
from app.templates_.repository import TemplateRepository
from app.templates_.service import TemplateService


def get_message_service(db: DbSession) -> MessageService:
    return MessageService(MessageRepository(db))


MessageServiceDep = Annotated[MessageService, Depends(get_message_service)]


def get_message_send_service(db: DbSession) -> MessageSendService:
    return MessageSendService(
        MessageRepository(db),
        ProjectService(ProjectRepository(db)),
        EmailTypeService(EmailTypeRepository(db)),
        TemplateService(TemplateRepository(db)),
    )


MessageSendServiceDep = Annotated[MessageSendService, Depends(get_message_send_service)]
