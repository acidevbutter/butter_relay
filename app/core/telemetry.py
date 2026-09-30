import logging
import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.db.session import engine
from app.settings.config import settings

logger = logging.getLogger(__name__)


class _NewRelicLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("opentelemetry")


def _configure_new_relic_log_handler(resource: Resource) -> None:
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    handler.name = "newrelic_otlp"
    handler.addFilter(_NewRelicLogFilter())
    logging.getLogger().addHandler(handler)


def configure_telemetry(app: FastAPI) -> bool:
    """Send APM traces and metrics through the configured OTLP/HTTP endpoint."""
    if not settings.otel_enabled:
        return False

    resource = Resource.create(
        {
            SERVICE_NAME: settings.otel_service_name,
            "deployment.environment.name": settings.environment,
        }
    )
    tracer_provider = TracerProvider(resource=resource)
    _configure_new_relic_log_handler(resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15_000)
        ],
    )
    metrics.set_meter_provider(meter_provider)

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        excluded_urls="health,health/ready",
    )
    HTTPXClientInstrumentor().instrument(tracer_provider=tracer_provider)
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    logger.info("OpenTelemetry enabled", extra={"service": settings.otel_service_name})
    return True
