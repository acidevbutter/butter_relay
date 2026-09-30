"""Compact, human-readable formatter for the application terminal."""

import logging
from datetime import UTC, datetime

_RESERVED = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}
_ALIASES = {
    "method": "http.request.method",
    "route": "url.path",
    "status_code": "http.response.status_code",
    "request_id": "request.id",
}
_CONSUMED = {
    "deployment_environment",
    "environment",
    "event",
    "log_type",
    "service",
    "service_name",
    *_ALIASES,
}


class ServiceContextFilter(logging.Filter):
    def __init__(self, service_name: str, environment: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        record.service_name = self.service_name
        record.deployment_environment = self.environment
        return True


class TerminalFormatter(logging.Formatter):
    """Render a short terminal line; OTLP keeps the full structured record."""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(
            record,
            "event",
            "metric_emitted" if record.name == "metrics" else "log",
        )
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        service = getattr(record, "service_name", "unknown")
        environment = getattr(record, "deployment_environment", "unknown")
        head = (
            f"{timestamp}Z {record.levelname:<7} [{service}/{environment}] "
            f"{event}: {record.getMessage()}"
        )
        details: list[str] = []
        if hasattr(record, "method"):
            details.append(f"{record.method} {getattr(record, 'route', '?')}")
        if hasattr(record, "status_code"):
            details.append(f"→ {record.status_code}")
        if hasattr(record, "duration_ms"):
            details.append(f"{record.duration_ms:.1f}ms")
        if hasattr(record, "request_id"):
            details.append(f"request_id={record.request_id}")
        line = f"{head} | {' | '.join(details)}" if details else head
        if record.exc_info:
            return f"{line}\n{self.formatException(record.exc_info)}"
        return line
