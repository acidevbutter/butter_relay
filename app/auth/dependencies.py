from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, Request

from app.auth.models import AdminUser
from app.auth.repository import AdminUserRepository
from app.auth.service import AuthService
from app.core.dependencies import DbSession
from app.core.exceptions import SessionRequiredError, UnauthorizedError


def get_auth_service(db: DbSession) -> AuthService:
    return AuthService(AdminUserRepository(db))


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_current_admin_user(
    request: Request, service: AuthServiceDep, db: DbSession
) -> AdminUser:
    """For HTML dashboard routes (`/admin/*`) -- no valid session redirects to the
    login page (see SessionRequiredError).

    The returned user is detached from the request's session: a rollback (e.g. a
    form that hit a unique constraint and re-renders with an error) expires every
    attached instance, and the page header reading `admin_user.email` afterwards
    raised MissingGreenlet -> HTTP 500.
    """
    admin_user_id = request.session.get("admin_user_id")
    if admin_user_id is None:
        raise SessionRequiredError()
    admin_user = await service.get(uuid.UUID(admin_user_id))
    if admin_user is None or not admin_user.is_active:
        raise SessionRequiredError()
    db.expunge(admin_user)
    return admin_user


CurrentAdminUser = Annotated[AdminUser, Depends(get_current_admin_user)]


async def get_current_admin_user_for_api(request: Request, service: AuthServiceDep) -> AdminUser:
    """For JSON routes (`/api/v1/*`) that are admin-only -- e.g. project management
    (see docs/auditoria-seguranca-2026-08-29.md item 1 / docs/prompts/autenticar-api-v1.md).
    Unlike `get_current_admin_user`, this returns a 401 JSON body instead of
    redirecting to the HTML login page, which would be wrong for an API client.
    """
    admin_user_id = request.session.get("admin_user_id")
    if admin_user_id is None:
        raise UnauthorizedError("Admin session required")
    admin_user = await service.get(uuid.UUID(admin_user_id))
    if admin_user is None or not admin_user.is_active:
        raise UnauthorizedError("Admin session required")
    return admin_user


CurrentAdminUserApi = Annotated[AdminUser, Depends(get_current_admin_user_for_api)]


async def get_current_admin_user_for_connect(
    request: Request, service: AuthServiceDep, db: DbSession
) -> AdminUser:
    """Like `get_current_admin_user`, but the login redirect remembers the consent page
    (query string included) so the staff member lands back on it after signing in."""
    admin_user_id = request.session.get("admin_user_id")
    back = "/admin/login?next=" + quote(
        request.url.path + (f"?{request.url.query}" if request.url.query else ""), safe=""
    )
    if admin_user_id is None:
        raise SessionRequiredError(back)
    admin_user = await service.get(uuid.UUID(admin_user_id))
    if admin_user is None or not admin_user.is_active:
        raise SessionRequiredError(back)
    db.expunge(admin_user)
    return admin_user


CurrentAdminUserForConnect = Annotated[AdminUser, Depends(get_current_admin_user_for_connect)]
