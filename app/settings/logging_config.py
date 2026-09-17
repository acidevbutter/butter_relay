import json
import logging.config
from datetime import UTC, datetime
from app.core.metrics import SERVICE_NAME
from app.settings.config import settings
from app.settings.terminal_logging import ServiceContextFilter, TerminalFormatter

_RESERVED_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}
_EVENT_ONLY_KEYS = {"event"}


class JsonFormatter(logging.Formatter):
    """Formats every log record as a single JSON object.

    Base fields are always present: timestamp, level, logger, event, service,
    environment. Anything passed via `extra={...}` (e.g. request_id, method,
    route, status_code, duration_ms) is merged in as-is, so callers control
    exactly which structured fields a given log line carries -- see
    app.settings.middleware.RequestContextMiddleware for the request-completion
    event and app.core.decorators for the method-call/error convention this
    follows (`extra={"event": ..., ...}`).
    """

    def format(self, record: logging.LogRecord) -> str:
        if record.name == "metrics":
            # app.core.metrics.emit_metrics already renders a flat, complete
            # JSON payload for New Relic ingestion -- wrapping it as this
            # formatter's own "event"/"message" string would nest it inside
            # another JSON object, hiding every metric field from New
            # Relic's top-level-key auto-parsing.
            return record.getMessage()
        # Imported lazily to avoid a hard import-time dependency from this
        # module (loaded very early, before settings are guaranteed ready)
        # on the rest of the app.
        from app.core.metrics import SERVICE_NAME
        from app.settings.config import settings

        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "service": SERVICE_NAME,
            "environment": settings.environment,
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key in _EVENT_ONLY_KEYS:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "terminal": {"()": TerminalFormatter},
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "terminal", "filters": ["service_context"]},
            },
            "filters": {"service_context": {"()": ServiceContextFilter, "service_name": SERVICE_NAME, "environment": settings.environment}},
            "root": {"handlers": ["console"], "level": level},
            "loggers": {
                "sqlalchemy.engine": {"level": "WARNING", "propagate": True},
                # uvicorn's own access logger would otherwise emit a second,
                # differently-shaped line for every request alongside the
                # structured "request_completed"/"request_failed" event from
                # RequestContextMiddleware. Quiet it instead of letting it
                # propagate, so exactly one completion log event exists per
                # request.
                "uvicorn.access": {"level": "WARNING", "propagate": False},
                "botocore": {"level": "WARNING", "propagate": True},
            },
        }
    )
