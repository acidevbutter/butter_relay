import logging
from typing import NoReturn
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.requests import Request

from app.core.email_provider import BrevoEmailProvider, EmailSendError
from app.core.metrics import (
    NAMESPACE,
    SERVICE_NAME,
    configure_metrics,
    emit_metrics,
    route_template,
    status_class,
)


def test_helpers_and_configuration() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/projects/abc",
            "headers": [],
            "route": type("Route", (), {"path": "/api/v1/projects/{project_id}"})(),
        }
    )
    unmatched_request = Request({"type": "http", "method": "GET", "path": "/nope", "headers": []})

    configure_metrics()

    assert route_template(request) == "/api/v1/projects/{project_id}"
    assert route_template(unmatched_request) == "unmatched"
    assert status_class(503) == "5xx"


async def test_emit_metrics_writes_one_structured_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="metrics"):
        await emit_metrics(
            [("RequestCount", 1, "Count")],
            {"Method": "GET", "Route": "/api/v1/projects/{project_id}", "StatusClass": "2xx"},
        )

    assert len(caplog.records) == 1
    payload = vars(caplog.records[0])
    assert payload["event"] == "metric_emitted"
    assert payload["service"] == SERVICE_NAME
    assert payload["metric_namespace"] == NAMESPACE
    assert payload["environment"] == "development"
    assert payload["Method"] == "GET"
    assert payload["Route"] == "/api/v1/projects/{project_id}"
    assert payload["StatusClass"] == "2xx"
    assert payload["RequestCount"] == 1
    assert payload["RequestCount_unit"] == "Count"


async def test_emit_metrics_without_dimensions(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="metrics"):
        await emit_metrics([("HealthCheckFailure", 1, "Count")])

    payload = vars(caplog.records[0])
    assert payload["HealthCheckFailure"] == 1
    assert payload["HealthCheckFailure_unit"] == "Count"


async def test_emit_metrics_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import app.core.metrics as metrics_module

    def _boom(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("stdout unavailable")

    monkeypatch.setattr(metrics_module, "_metrics_logger", type("_Boom", (), {"info": _boom})())

    with caplog.at_level(logging.ERROR):
        await emit_metrics([("RequestCount", 1, "Count")])

    assert "failed to emit metrics" in caplog.text


async def test_metric_emit_failure_does_not_break_request(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.core.metrics as metrics_module

    def _boom(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("stdout unavailable")

    monkeypatch.setattr(metrics_module, "_metrics_logger", type("_Boom", (), {"info": _boom})())

    response = await client.get("/health")

    assert response.status_code == 200


async def test_readiness_reports_database_failure_and_metric(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.main as main_module

    class UnavailableSession:
        async def __aenter__(self) -> NoReturn:
            raise OSError("database unavailable")

        async def __aexit__(self, *exc: object) -> bool:
            return False

    emit_mock = AsyncMock()
    monkeypatch.setattr(main_module, "session_factory", lambda: UnavailableSession())
    monkeypatch.setattr(main_module, "emit_metrics", emit_mock)

    response = await client.get("/health/ready")

    assert response.status_code == 503
    emit_mock.assert_awaited_once_with([("HealthCheckFailure", 1, "Count")])


async def test_health_probe_does_not_emit_request_metrics(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="metrics"):
        response = await client.get("/health")

    assert response.status_code == 200
    metric_records = [r for r in caplog.records if r.name == "metrics"]
    assert metric_records == []


@pytest.mark.parametrize("status_code", [400, 500])
async def test_brevo_failures_emit_error_metric(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    import app.core.email_provider as email_provider_module

    emit_mock = AsyncMock()
    monkeypatch.setattr(email_provider_module, "emit_metrics", emit_mock)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, json={"message": "failed"})
        )
    )
    provider = BrevoEmailProvider(api_key="test-key", client=client)

    with pytest.raises(EmailSendError):
        await provider.send(
            to="recipient@example.com",
            subject="Subject",
            html="<p>Body</p>",
            from_email="sender@example.com",
        )

    emitted_names = [metric[0] for call in emit_mock.await_args_list for metric in call.args[0]]
    assert emitted_names == ["EmailProviderLatency", "EmailProviderErrorCount"]
    assert ("EmailProviderErrorCount", 1, "Count") in emit_mock.await_args_list[-1].args[0]
    await client.aclose()
