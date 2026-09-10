from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse

from app.admin.templating import templates
from app.auth.dependencies import AuthServiceDep
from app.core.exceptions import UnauthorizedError

router = APIRouter(prefix="/admin", tags=["auth"])


@router.get("/login", summary="Show the admin login form")
async def login_form(request: Request) -> Response:
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", summary="Authenticate and start an admin session")
async def login(
    request: Request,
    service: AuthServiceDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    try:
        admin_user = await service.authenticate(email, password)
    except UnauthorizedError as exc:
        return templates.TemplateResponse(
            request, "login.html", {"error": exc.detail}, status_code=401
        )
    request.session["admin_user_id"] = str(admin_user.id)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/logout", summary="End the admin session")
async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)
