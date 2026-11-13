from typing import Annotated

from fastapi import Depends

from app.api_keys.repository import ApiKeyRepository
from app.api_keys.service import ApiKeyService
from app.core.dependencies import DbSession
from app.pairing.repository import PairingRepository
from app.pairing.service import PairingService
from app.projects.repository import ProjectRepository


def get_pairing_service(db: DbSession) -> PairingService:
    projects = ProjectRepository(db)
    return PairingService(
        PairingRepository(db), projects, ApiKeyService(ApiKeyRepository(db), projects)
    )


PairingServiceDep = Annotated[PairingService, Depends(get_pairing_service)]
