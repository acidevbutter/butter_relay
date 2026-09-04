import logging
import time
import uuid

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.metrics import emit_metrics, route_template, status_class
from app.settings.config import settings

logger = logging.getLogger("app.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, times the request, and logs one line per request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request %s failed: %s %s", request_id, request.method, request.url.path
            )
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s -> %s (%.1fms) [%s]",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        metrics = [
            ("RequestCount", 1, "Count"),
            ("Latency", duration_ms, "Milliseconds"),
        ]
        if response.status_code >= 500:
            metrics.append(("5xxCount", 1, "Count"))
        elif response.status_code >= 400:
            metrics.append(("4xxCount", 1, "Count"))
        await emit_metrics(
            metrics,
            {
                "Method": request.method,
                "Route": route_template(request),
                "StatusClass": status_class(response.status_code),
            },
        )
        return response


def register_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie=settings.session_cookie_name,
        https_only=not settings.debug,
    )
