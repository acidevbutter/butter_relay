import logging.config

from app.settings.config import SERVICE_NAME, settings
from app.settings.terminal_logging import ServiceContextFilter, TerminalFormatter


def setup_logging(level: str = "INFO") -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "terminal": {"()": TerminalFormatter},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "terminal",
                    "filters": ["service_context"],
                },
            },
            "filters": {
                "service_context": {
                    "()": ServiceContextFilter,
                    "service_name": SERVICE_NAME,
                    "environment": settings.environment,
                }
            },
            "root": {"handlers": ["console"], "level": level},
            "loggers": {
                "sqlalchemy.engine": {"level": "WARNING", "propagate": True},
                # uvicorn's own access logger would otherwise emit a second,
                # differently-shaped line for every request alongside the
                # single "request_completed"/"request_failed" event from
                # RequestContextMiddleware. Quiet it instead of letting it
                # propagate, so exactly one completion log line exists per
                # request.
                "uvicorn.access": {"level": "WARNING", "propagate": False},
                "botocore": {"level": "WARNING", "propagate": True},
                "httpx": {"level": "WARNING", "propagate": True},
            },
        }
    )
