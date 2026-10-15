import logging
import secrets
import time
import uuid

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.settings.config import settings

logger = logging.getLogger("app.request")
_PROBE_PATHS = frozenset({"/health", "/health/ready"})


def route_template(request: Request) -> str:
    """Return the low-cardinality route template, never a raw path with IDs."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, times the request, and logs one line per request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            # Exactly one completion event per request: this is the *only*
            # log line emitted for a request that raises an unhandled
            # exception. Deliberately excludes headers/body/payload -- this
            # service relays webhooks/messages, so nothing here should ever
            # carry secrets, tokens, or inbound payloads (see
            # docs/prompts/redacao-dados-sensiveis-logs.md). exc_info carries
            # the traceback for correlation; request_id ties it back to the
            # client-visible X-Request-ID on the eventual error response.
            logger.exception(
                "request failed: %s %s",
                request.method,
                route_template(request),
                extra={
                    "event": "request_failed",
                    "request_id": request_id,
                    "method": request.method,
                    "route": route_template(request),
                    "duration_ms": round(duration_ms, 1),
                },
            )
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        if request.url.path in _PROBE_PATHS:
            return response
        route = route_template(request)
        # Exactly one completion event per request: this is the only log line
        # for a request that completes (successfully or with a handled error
        # response). uvicorn's access log is quieted in logging_config.py so
        # it cannot duplicate this line.
        logger.info(
            "%s %s -> %s (%.1fms)",
            request.method,
            route,
            response.status_code,
            duration_ms,
            extra={
                "event": "request_completed",
                "request_id": request_id,
                "method": request.method,
                "route": route,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 1),
            },
        )
        return response


def build_csp(nonce: str) -> str:
    """CSP for the admin HTML pages (DVB-205). Scripts need the per-request nonce
    (plus the pinned htmx CDN), so injected inline script/handlers never run; the
    template previews are sandboxed srcdoc iframes that inherit this policy too.
    Remote images stay allowed so e-mail previews with hosted logos render.
    """
    return "; ".join(
        [
            "default-src 'none'",
            f"script-src 'nonce-{nonce}' https://unpkg.com",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: https:",
            "connect-src 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "object-src 'none'",
        ]
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defensive headers on every response, plus a nonce-based CSP on HTML pages."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if response.headers.get("content-type", "").startswith("text/html") and not (
            request.url.path.startswith(("/docs", "/redoc"))
        ):
            response.headers["Content-Security-Policy"] = build_csp(nonce)
        return response


def register_middleware(app: FastAPI) -> None:
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie=settings.session_cookie_name,
        https_only=not settings.debug,
    )
