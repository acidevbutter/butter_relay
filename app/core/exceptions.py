from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

Handler = Callable[[Request, Exception], Awaitable[Response]]
_registry: dict[type[Exception], Handler] = {}


def exception_handler(exc_type: type[Exception]) -> Callable[[Handler], Handler]:
    """Decorator that registers a handler function for an exception type.
    Collect all of these onto the app with register_exception_handlers(app).
    """
    def decorator(func: Handler) -> Handler:
        _registry[exc_type] = func
        return func
    return decorator


def register_exception_handlers(app: FastAPI) -> None:
    for exc_type, handler in _registry.items():
        app.add_exception_handler(exc_type, handler)


class NotFoundError(Exception):
    def __init__(self, detail: str = "Resource not found"):
        self.detail = detail


class ConflictError(Exception):
    def __init__(self, detail: str = "Conflict"):
        self.detail = detail


class UnauthorizedError(Exception):
    def __init__(self, detail: str = "Unauthorized"):
        self.detail = detail


class ForbiddenError(Exception):
    """Raised when the caller is authenticated but not allowed to act on the
    requested resource -- e.g. an API key from one project trying to read or
    manage another project's data. See docs/auditoria-seguranca-2026-08-29.md.
    """
    def __init__(self, detail: str = "Forbidden"):
        self.detail = detail


class ValidationFailedError(Exception):
    def __init__(self, detail: str = "Validation failed"):
        self.detail = detail


class SessionRequiredError(Exception):
    """Raised by the admin-dashboard auth dependency when no valid session cookie
    is present. Redirects to the login page instead of returning a JSON 401,
    since every route that raises this is an HTML dashboard page.
    """
    def __init__(self, redirect_to: str = "/admin/login"):
        self.redirect_to = redirect_to


@exception_handler(NotFoundError)
async def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": exc.detail})


@exception_handler(ConflictError)
async def handle_conflict(request: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": exc.detail})


@exception_handler(UnauthorizedError)
async def handle_unauthorized(request: Request, exc: UnauthorizedError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": exc.detail})


@exception_handler(ForbiddenError)
async def handle_forbidden(request: Request, exc: ForbiddenError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": exc.detail})


@exception_handler(ValidationFailedError)
async def handle_validation_failed(request: Request, exc: ValidationFailedError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": exc.detail}
    )


@exception_handler(SessionRequiredError)
async def handle_session_required(request: Request, exc: SessionRequiredError) -> RedirectResponse:
    return RedirectResponse(url=exc.redirect_to, status_code=status.HTTP_303_SEE_OTHER)
