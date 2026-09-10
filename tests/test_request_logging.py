import json
import logging

import httpx
import pytest

from app.settings.logging_config import JsonFormatter

REQUEST_LOGGER = "app.request"


def _format(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(record))


async def test_one_completion_event_per_successful_request(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=REQUEST_LOGGER):
        response = await client.get("/health")

    assert response.status_code == 200
    request_records = [r for r in caplog.records if r.name == REQUEST_LOGGER]
    assert len(request_records) == 1

    payload = _format(request_records[0])
    assert payload["event"] == "request_completed"
    assert payload["logger"] == REQUEST_LOGGER
    assert payload["level"] == "INFO"
    assert payload["service"] == "butter-relay"
    assert payload["environment"] == "development"
    assert payload["method"] == "GET"
    assert payload["route"] == "/health"
    assert payload["status_code"] == 200
    assert isinstance(payload["duration_ms"], int | float)
    assert "request_id" in payload and payload["request_id"]


async def test_route_field_is_template_not_raw_path_with_ids(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=REQUEST_LOGGER):
        response = await client.get("/api/v1/projects/does-not-exist")

    request_records = [r for r in caplog.records if r.name == REQUEST_LOGGER]
    assert len(request_records) == 1
    payload = _format(request_records[0])
    assert response.status_code in (401, 403, 404)
    assert "does-not-exist" not in payload["route"]


async def test_one_failure_event_per_unhandled_exception(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    from app.main import app

    async def _boom() -> dict[str, str]:
        raise RuntimeError("boom")

    app.add_api_route("/__boom", _boom, methods=["GET"])
    try:
        with caplog.at_level(logging.INFO, logger=REQUEST_LOGGER), pytest.raises(RuntimeError):
            await client.get("/__boom")
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/__boom"]

    request_records = [r for r in caplog.records if r.name == REQUEST_LOGGER]
    assert len(request_records) == 1

    record = request_records[0]
    payload = _format(record)
    assert payload["event"] == "request_failed"
    assert payload["level"] == "ERROR"
    assert payload["method"] == "GET"
    assert payload["route"] == "/__boom"
    assert "request_id" in payload and payload["request_id"]
    assert "exc_info" in payload
    assert "boom" in payload["exc_info"]


def test_json_formatter_never_includes_raw_message_placeholders() -> None:
    record = logging.LogRecord(
        name="app.request",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="%s %s -> %s (%.1fms)",
        args=("GET", "/health", 200, 1.234),
        exc_info=None,
    )
    record.event = "request_completed"
    record.request_id = "abc-123"
    record.method = "GET"
    record.route = "/health"
    record.status_code = 200
    record.duration_ms = 1.2

    payload = _format(record)

    assert payload["event"] == "request_completed"
    assert payload["request_id"] == "abc-123"
    assert payload["route"] == "/health"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 1.2
    # No secrets/payload keys ever end up in a request-completion log.
    assert "payload" not in payload
    assert "headers" not in payload


def test_uvicorn_access_logger_is_quiet() -> None:
    logger = logging.getLogger("uvicorn.access")
    assert logger.propagate is False
    assert logger.level == logging.WARNING
