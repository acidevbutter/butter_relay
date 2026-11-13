"""HTML consent page: a staff member approves connecting a WordPress site to a project.

GET  /connect          public host entry point; sends the browser to the admin host
GET  /admin/connect    consent form (admin session; login then comes back here)
POST /admin/connect    approve: mints the code and sends the browser back to the site
"""

import secrets
import uuid
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from app.admin.templating import templates
from app.auth.dependencies import CurrentAdminUserForConnect
from app.auth.models import AdminUser
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.pairing.dependencies import PairingServiceDep
from app.pairing.models import ConnectRequest
from app.pairing.service import suggested_slug, validate_return_url
from app.projects.dependencies import ProjectServiceDep
from app.projects.schemas import ProjectCreate
from app.projects.service import ProjectService
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


def _page(request: Request, context: dict[str, Any], status_code: int = 200) -> HTMLResponse:
    csrf = secrets.token_urlsafe(24)
    request.session["connect_csrf"] = csrf
    return templates.TemplateResponse(
        request, "connect.html", {**context, "csrf": csrf}, status_code=status_code
    )


async def _form_context(
    admin_user: AdminUser,
    projects: ProjectService,
    *,
    request_id: str = "",
    pending: ConnectRequest | None = None,
    return_url: str = "",
    state: str = "",
    site_host: str = "",
    error: str | None = None,
    form: dict[str, str] | None = None,
) -> dict[str, Any]:
    active = [p for p in await projects.list() if p.is_active]
    host = pending.site_host if pending else site_host
    slug = suggested_slug(host)
    # A project made for this site before is preselected; otherwise "create new".
    existing = next((p for p in active if p.slug == slug), None)
    new_defaults = {
        "new_name": pending.site_name if pending else host,
        "new_slug": slug,
        "new_sender_email": pending.sender_email if pending else "",
        "new_sender_name": pending.sender_name if pending else "",
    }
    return {
        "admin_user": admin_user,
        "projects": active,
        "request_id": request_id,
        "pending": pending,
        "return_url": return_url,
        "state": state,
        "site_host": host,
        "choice": (form or {}).get("project_id") or (str(existing.id) if existing else "new"),
        "new": {**new_defaults, **{k: v for k, v in (form or {}).items() if k.startswith("new_")}},
        "error": error,
    }


@router.get("/connect", summary="Consent page to connect a WordPress site")
async def connect_form(
    request: Request,
    admin_user: CurrentAdminUserForConnect,
    projects: ProjectServiceDep,
    service: PairingServiceDep,
    request_id: Annotated[str, Query(alias="request")] = "",
    return_url: str = "",
    state: str = "",
    site: str = "",
) -> HTMLResponse:
    if request_id:
        pending = await service.open_request(request_id)
        context = await _form_context(admin_user, projects, request_id=request_id, pending=pending)
        return _page(request, context)
    # Plugin 0.5.0 sends the return URL and state in the link itself.
    site_host = validate_return_url(return_url)
    context = await _form_context(
        admin_user, projects, return_url=return_url, state=_clean_state(state), site_host=site_host
    )
    return _page(request, context)


@router.post("/connect", summary="Approve the connection and return to the site")
async def connect_approve(
    request: Request,
    admin_user: CurrentAdminUserForConnect,
    service: PairingServiceDep,
    projects: ProjectServiceDep,
    csrf: Annotated[str, Form()],
    project_id: Annotated[str, Form()] = "",
    request_id: Annotated[str, Form()] = "",
    return_url: Annotated[str, Form()] = "",
    state: Annotated[str, Form()] = "",
    label: Annotated[str, Form()] = "",
    new_name: Annotated[str, Form()] = "",
    new_slug: Annotated[str, Form()] = "",
    new_sender_email: Annotated[str, Form()] = "",
    new_sender_name: Annotated[str, Form()] = "",
) -> HTMLResponse:
    expected = request.session.pop("connect_csrf", None)
    if not expected or not secrets.compare_digest(expected, csrf):
        raise ValidationFailedError("Session expired, open the connect link again")

    if not request_id:  # Plugin 0.5.0 flow: existing project only.
        site_host = validate_return_url(return_url)
        try:
            code = await service.approve(uuid.UUID(project_id), label, site_host, admin_user.email)
        except ValueError as exc:
            raise ValidationFailedError("Pick a project") from exc
        except NotFoundError as exc:
            raise ValidationFailedError("Project not found or inactive") from exc
        return _done(request, admin_user, return_url, code, _clean_state(state), site_host)

    form = {
        "project_id": project_id,
        "new_name": new_name,
        "new_slug": new_slug,
        "new_sender_email": new_sender_email,
        "new_sender_name": new_sender_name,
    }
    try:
        new_project = None
        chosen = None
        if project_id == "new":
            new_project = ProjectCreate(
                name=new_name.strip(),
                slug=new_slug.strip(),
                external=True,
                default_sender_email=new_sender_email.strip(),
                default_sender_name=new_sender_name.strip(),
            )
        else:
            chosen = uuid.UUID(project_id)
        code, pending, _ = await service.approve_request(
            request_id, chosen, new_project, label, admin_user.email
        )
    except (ValidationError, ValueError, ConflictError, ValidationFailedError) as exc:
        message = getattr(exc, "detail", None) or str(exc).splitlines()[0]
        pending = await service.open_request(request_id)
        context = await _form_context(
            admin_user, projects, request_id=request_id, pending=pending, error=message, form=form
        )
        return _page(request, context, status_code=422)
    return _done(request, admin_user, pending.return_url, code, pending.state, pending.site_host)


def _done(
    request: Request, admin_user: AdminUser, return_url: str, code: str, state: str, site_host: str
) -> HTMLResponse:
    separator = "&" if "?" in return_url else "?"
    target = return_url + separator + urlencode({"code": code, "state": state})
    # Not a 303: the page's form-action CSP would block a cross-site redirect after a form POST.
    return templates.TemplateResponse(
        request,
        "connect_done.html",
        {"admin_user": admin_user, "target": target, "site_host": site_host},
    )
