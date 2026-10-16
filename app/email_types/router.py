from fastapi import APIRouter

from app.api_keys.dependencies import ReadApiKey
from app.email_types.dependencies import EmailTypeServiceDep
from app.email_types.models import EmailType
from app.email_types.schemas import EmailTypeRead

router = APIRouter(prefix="/api/v1/email-types", tags=["email-types"])


@router.get(
    "",
    response_model=list[EmailTypeRead],
    summary="E-mail types this client may send (auto-discovery)",
)
async def list_email_types(
    current_api_key: ReadApiKey, service: EmailTypeServiceDep
) -> list[EmailType]:
    # Scoped to the caller's own project: the key decides, never a query parameter.
    return await service.list(current_api_key.project_id, active_only=True)
