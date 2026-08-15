import logging

import pytest

from app.core.decorators import log_call, log_errors


def test_log_call_sync_logs_debug_and_returns_value(caplog):
    @log_call
    def add(a, b):
        return a + b

    with caplog.at_level(logging.DEBUG):
        result = add(1, 2)

    assert result == 3
    assert any(r.levelname == "DEBUG" and "add" in r.message for r in caplog.records)


def test_log_call_sync_raises_and_logs_error(caplog):
    @log_call
    def boom():
        raise ValueError("bad")

    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError):
        boom()

    assert any(r.levelname == "ERROR" for r in caplog.records)


async def test_log_call_async_returns_value_and_logs(caplog):
    @log_call
    async def add_async(a, b):
        return a + b

    with caplog.at_level(logging.DEBUG):
        result = await add_async(2, 3)

    assert result == 5
    assert any(r.levelname == "DEBUG" for r in caplog.records)


def test_log_call_records_carry_event_and_target(caplog):
    @log_call
    def noop():
        return None

    with caplog.at_level(logging.DEBUG):
        noop()

    record = next(r for r in caplog.records if r.levelname == "DEBUG")
    assert record.event == "method_call"
    assert "noop" in record.target


def test_log_errors_wraps_public_methods_not_dunder_or_private(caplog):
    @log_errors
    class Widget:
        def __init__(self):
            self.value = 1

        def public_method(self):
            return self.value

        def _private_method(self):
            return self.value

    widget = Widget()
    with caplog.at_level(logging.DEBUG):
        widget.public_method()
        widget._private_method()

    called_targets = [
        r.target for r in caplog.records if getattr(r, "event", None) == "method_call"
    ]
    assert any("public_method" in t for t in called_targets)
    assert not any("_private_method" in t for t in called_targets)
    assert not any("__init__" in t for t in called_targets)
