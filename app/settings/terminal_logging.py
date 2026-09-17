import json
import logging
from datetime import UTC, datetime

_RESERVED = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}
class ServiceContextFilter(logging.Filter):
    def __init__(self, service_name: str, environment: str): super().__init__(); self.service_name, self.environment = service_name, environment
    def filter(self, record): record.service_name, record.deployment_environment = self.service_name, self.environment; return True
class TerminalFormatter(logging.Formatter):
    def format(self, record):
        def value(v):
            t = str(v); return json.dumps(t) if not t or any(c.isspace() for c in t) else t
        event = getattr(record, "event", "metric_emitted" if record.name == "metrics" else "log")
        kind = getattr(record, "log_type", "metric" if record.name == "metrics" else "error" if record.levelno >= logging.ERROR else "access" if record.name == "app.request" else "application")
        fields = {"timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(), "service.name": getattr(record, "service_name", "unknown"), "deployment.environment": getattr(record, "deployment_environment", "unknown"), "log.severity": record.levelname, "event.name": event, "log.type": kind, "logger": record.name, "message": record.getMessage()}
        for src, dst in {"method":"http.request.method", "route":"url.path", "status_code":"http.response.status_code", "request_id":"request.id"}.items():
            if hasattr(record, src): fields[dst] = getattr(record, src)
        return " ".join(f"{k}={value(v)}" for k, v in fields.items())
