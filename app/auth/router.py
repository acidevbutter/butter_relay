import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.admin.templating import templates
from app.auth.models import AdminUser
from app.auth.repository import AdminUserRepository
from app.auth.service import AuthService
from app.core.dependencies import DbSession
from app.core.exceptions import SessionRequiredError, UnauthorizedError


def get_auth_service(db: DbSession) -> AuthService:
    return AuthService(AdminUserRepository(db))


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_current_admin_user(request: Request, service: AuthServiceDep) -> AdminUser:
    admin_user_id = request.session.get("admin_user_id")
    if admin_user_id is None:
        raise SessionRequiredError()
    admin_user = await service.get(uuid.UUID(admin_user_id))
    if admin_user is None or not admin_user.is_active:
        raise SessionRequiredError()
    return admin_user


CurrentAdminUser = Annotated[AdminUser, Depends(get_current_admin_user)]

router = APIRouter(prefix="/admin", tags=["auth"])


@router.get("/login", summary="Show the admin login form")
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", summary="Authenticate and start an admin session")
async def login(
    request: Request,
    service: AuthServiceDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    try:
        admin_user = await service.authenticate(email, password)
    except UnauthorizedError as exc:
        return templates.TemplateResponse(
            request, "login.html", {"error": exc.detail}, status_code=401
        )
    request.session["admin_user_id"] = str(admin_user.id)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/logout", summary="End the admin session")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)
