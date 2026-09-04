"""CloudWatch Embedded Metric Format helpers for Butter Relay."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence

from aws_embedded_metrics.config import get_config
from aws_embedded_metrics.logger.metrics_logger_factory import create_metrics_logger
from starlette.requests import Request

from app.settings.config import settings

logger = logging.getLogger(__name__)

NAMESPACE = "DevButter/ButterRelay"
SERVICE_NAME = "butter-relay"
LOG_GROUP_NAME = "/ecs/devbutter/staging/relay"

Metric = tuple[str, float, str]


def configure_metrics() -> None:
    """Configure EMF metadata for the relay's existing CloudWatch log group.

    Forces the "Local" EMF environment (stdout sink) by default: this
    architecture ships metrics via the ECS `awslogs` log driver -> CloudWatch
    Logs auto-extraction, never a CloudWatch Agent sidecar. Without this
    override the library's environment autodetection falls through to the
    "Agent" sink (TCP to a nonexistent daemon), which drops every metric and
    spams "Connection refused"/"Broken pipe" on every request, locally and in
    production alike.
    """
    config = get_config()
    config.namespace = NAMESPACE
    config.service_name = SERVICE_NAME
    config.log_group_name = LOG_GROUP_NAME
    config.environment = os.getenv("AWS_EMF_ENVIRONMENT", "Local")


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
    """Emit a metric batch without allowing telemetry failures to affect work."""
    try:
        metric_logger = create_metrics_logger()
        metric_logger.set_namespace(NAMESPACE)
        coarse_dimensions = {"Environment": settings.environment, "Service": SERVICE_NAME}
        detailed_dimensions = {**coarse_dimensions, **(dimensions or {})}
        metric_logger.set_dimensions(coarse_dimensions, detailed_dimensions)
        for name, value, unit in metrics:
            metric_logger.put_metric(name, value, unit)
        await metric_logger.flush()
    except Exception:
        logger.exception("failed to emit CloudWatch EMF metrics")
