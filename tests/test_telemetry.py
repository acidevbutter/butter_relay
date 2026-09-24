from fastapi import FastAPI

from app.core.telemetry import configure_telemetry
from app.settings.config import settings


def test_telemetry_is_disabled_without_runtime_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(settings, "otel_enabled", False)

    assert configure_telemetry(FastAPI()) is False


def test_telemetry_service_name_matches_deployment_identity() -> None:
    assert settings.otel_service_name == "butter-relay"
