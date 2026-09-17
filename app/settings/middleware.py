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
                "Route": route,
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
