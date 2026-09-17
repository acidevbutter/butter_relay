"""Structured JSON metrics for New Relic log ingestion.

Replaces the previous CloudWatch Embedded Metric Format (EMF) sink
(``aws_embedded_metrics``). Metrics are written as single-line JSON via the
standard ``logging`` module instead of the EMF wire format: this service's
stdout is picked up by the New Relic OpenTelemetry collector (nrdot-collector)
running on the host, which classifies log lines by their ``message`` field
and routes metric events to New Relic's ``Log_metric`` partition -- no
agent-side SDK, no new dependency. New Relic auto-parses JSON log lines and
promotes their top-level keys to queryable log attributes.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from starlette.requests import Request

from app.settings.config import settings

logger = logging.getLogger(__name__)
_metrics_logger = logging.getLogger("metrics")

NAMESPACE = "DevButter/ButterRelay"
SERVICE_NAME = "butter-relay"

Metric = tuple[str, float, str]


def configure_metrics() -> None:
    """No-op retained for main.py's startup call. Metrics used to ship via
    CloudWatch Embedded Metric Format (aws_embedded_metrics), which needed a
    get_config() singleton configured before the first emit; the New Relic
    JSON sink below (see emit_metrics) carries no equivalent global state,
    so there is nothing left to configure here.
    """


def route_template(request: Request) -> str:
    """Return the low-cardinality route template, never a raw path with IDs."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"


async def emit_metrics(
    metrics: Sequence[Metric], dimensions: Mapping[str, str] | None = None
) -> None:
    """Write one structured metric log line without allowing telemetry
    failures to affect work.

    This ships as a single JSON line via the standard `logging` module
    instead of CloudWatch EMF: this service's stdout is picked up by the New
    Relic OpenTelemetry collector (nrdot-collector) running on the host, which
    classifies log lines by their `message` field and routes metric events to
    New Relic's `Log_metric` partition -- no agent-side SDK, no new
    dependency.
    """
    try:
        payload: dict[str, object] = {
            "event": "metric_emitted",
            "log_type": "metric",
            "service": SERVICE_NAME,
            "environment": settings.environment,
            "metric_namespace": NAMESPACE,
            **(dimensions or {}),
        }
        for name, value, unit in metrics:
            payload[name] = value
            payload[f"{name}_unit"] = unit
        _metrics_logger.info("metric emitted", extra=payload)
    except Exception:
        logger.exception("failed to emit metrics")
