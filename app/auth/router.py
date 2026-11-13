from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse

from app.admin.templating import templates
from app.auth.dependencies import AuthServiceDep
from app.core.exceptions import UnauthorizedError

router = APIRouter(prefix="/admin", tags=["auth"])


def _safe_next(value: str) -> str:
    """Only the pairing consent page may be resumed after login (no open redirect)."""
    return (
        value
        if value.startswith("/admin/connect") and "//" not in value and "\\" not in value
        else ""
    )


@router.get("/login", summary="Show the admin login form")
async def login_form(request: Request, next: str = "") -> Response:
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "next": _safe_next(next)}
    )


@router.post("/login", summary="Authenticate and start an admin session")
async def login(
    request: Request,
    service: AuthServiceDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
) -> Response:
    try:
        admin_user = await service.authenticate(email, password)
    except UnauthorizedError as exc:
        return templates.TemplateResponse(
            request, "login.html", {"error": exc.detail, "next": _safe_next(next)}, status_code=401
        )
    request.session["admin_user_id"] = str(admin_user.id)
    return RedirectResponse(url=_safe_next(next) or "/admin", status_code=303)


@router.post("/logout", summary="End the admin session")
async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)
