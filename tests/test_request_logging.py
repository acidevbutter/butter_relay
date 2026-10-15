import logging

import httpx
import pytest

from app.settings.terminal_logging import ServiceContextFilter, TerminalFormatter

REQUEST_LOGGER = "app.request"


def _format(record: logging.LogRecord) -> str:
    ServiceContextFilter("butter-relay", "test").filter(record)
    return TerminalFormatter().format(record)


async def test_health_probe_does_not_emit_completion_event(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=REQUEST_LOGGER):
        response = await client.get("/health")

    assert response.status_code == 200
    request_records = [r for r in caplog.records if r.name == REQUEST_LOGGER]
    assert request_records == []


async def test_route_field_is_template_not_raw_path_with_ids(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=REQUEST_LOGGER):
        response = await client.get("/api/v1/projects/does-not-exist")

    request_records = [r for r in caplog.records if r.name == REQUEST_LOGGER]
    assert len(request_records) == 1
    line = _format(request_records[0])
    assert response.status_code in (401, 403, 404)
    assert "does-not-exist" not in line
    assert "/api/v1/projects/{project_id}" in line


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
    line = _format(record)
    assert record.levelname == "ERROR"
    assert line.startswith(tuple("0123456789"))
    assert "ERROR" in line
    assert "[butter-relay/test] request_failed: request failed: GET /__boom" in line
    assert "| GET /__boom |" in line
    assert f"request_id={record.__dict__["request_id"]}" in line
    # Traceback follows the single summary line.
    assert "RuntimeError: boom" in line
    assert len(line.splitlines()[0]) < 200


def test_terminal_formatter_renders_one_readable_line() -> None:
    record = logging.LogRecord(
        name="app.request",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="%s %s -> %s (%.1fms)",
        args=("POST", "/api/v1/messages/", 202, 12.34),
        exc_info=None,
    )
    record.event = "request_completed"
    record.request_id = "abc-123"
    record.method = "POST"
    record.route = "/api/v1/messages/"
    record.status_code = 202
    record.duration_ms = 12.3

    line = _format(record)

    assert "\n" not in line
    assert (
        "INFO    [butter-relay/test] request_completed: POST /api/v1/messages/ -> 202 (12.3ms)"
        " | POST /api/v1/messages/ | → 202 | 12.3ms | request_id=abc-123"
    ) in line
    # Raw placeholders, payloads and headers never leak into the line.
    assert "%s" not in line
    assert "headers" not in line


def test_terminal_formatter_has_no_metric_events() -> None:
    record = logging.LogRecord(
        name="app.any", level=logging.INFO, pathname=__file__, lineno=1, msg="hi", args=(),
        exc_info=None,
    )
    assert "metric" not in _format(record)


def test_uvicorn_access_logger_is_quiet() -> None:
    logger = logging.getLogger("uvicorn.access")
    assert logger.propagate is False
    assert logger.level == logging.WARNING
