"""HTML consent page: a staff member approves connecting a WordPress site to a project.

GET  /connect          public host entry point; sends the browser to the admin host
GET  /admin/connect    consent form (admin session; login then comes back here)
POST /admin/connect    approve: mints the code and sends the browser back to the site
"""

import secrets
import uuid
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.admin.templating import templates
from app.auth.dependencies import CurrentAdminUserForConnect
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.pairing.dependencies import PairingServiceDep
from app.pairing.service import validate_return_url
from app.projects.dependencies import ProjectServiceDep
from app.settings.config import settings

public_router = APIRouter(tags=["pairing"])
router = APIRouter(prefix="/admin", tags=["pairing"])

_STATE_MAX = 200


def _clean_state(state: str) -> str:
    state = state.strip()
    if not state or len(state) > _STATE_MAX:
        raise ValidationFailedError("Missing or oversized state")
    return state


@public_router.get("/connect", summary="Entry point of the pairing link", include_in_schema=False)
async def connect_entry(request: Request) -> Response:
    base = settings.admin_base_url.rstrip("/")
    query = request.url.query
    url = f"{base}/admin/connect" + (f"?{query}" if query else "")
    return RedirectResponse(url=url, status_code=303)


@router.get("/connect", summary="Consent page to connect a WordPress site")
async def connect_form(
    request: Request,
    admin_user: CurrentAdminUserForConnect,
    projects: ProjectServiceDep,
    return_url: str = "",
    state: str = "",
    site: str = "",
) -> HTMLResponse:
    site_host = validate_return_url(return_url)
    csrf = secrets.token_urlsafe(24)
    request.session["connect_csrf"] = csrf
    active = [p for p in await projects.list() if p.is_active]
    return templates.TemplateResponse(
        request,
        "connect.html",
        {
            "admin_user": admin_user,
            "projects": active,
            "return_url": return_url,
            "state": _clean_state(state),
            "site_host": site_host,
            "csrf": csrf,
            "error": None,
        },
    )


@router.post("/connect", summary="Approve the connection and return to the site")
async def connect_approve(
    request: Request,
    admin_user: CurrentAdminUserForConnect,
    service: PairingServiceDep,
    return_url: Annotated[str, Form()],
    state: Annotated[str, Form()],
    project_id: Annotated[str, Form()],
    csrf: Annotated[str, Form()],
    label: Annotated[str, Form()] = "",
) -> HTMLResponse:
    expected = request.session.pop("connect_csrf", None)
    if not expected or not secrets.compare_digest(expected, csrf):
        raise ValidationFailedError("Session expired, open the connect link again")
    site_host = validate_return_url(return_url)
    try:
        code = await service.approve(uuid.UUID(project_id), label, site_host, admin_user.email)
    except ValueError as exc:
        raise ValidationFailedError("Pick a project") from exc
    except NotFoundError as exc:
        raise ValidationFailedError("Project not found or inactive") from exc
    separator = "&" if "?" in return_url else "?"
    target = return_url + separator + urlencode({"code": code, "state": _clean_state(state)})
    # Not a 303: the page's form-action CSP would block a cross-site redirect after a form POST.
    return templates.TemplateResponse(
        request,
        "connect_done.html",
        {"admin_user": admin_user, "target": target, "site_host": site_host},
    )
