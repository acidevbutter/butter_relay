"""DVB-456: a task the worker cannot parse is recorded and ends FAILURE, never
"succeeded" with the message silently dropped."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.worker.tasks as tasks_module
from app.rejected_messages.repository import RejectedMessageRepository
from app.worker.tasks import MalformedMessageError, process_message_task, record_rejection


@pytest.fixture
def patched_session(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> None:
    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(tasks_module, "session_factory", lambda: _SessionCtx())
    monkeypatch.setattr(tasks_module, "engine", AsyncMock())


async def test_record_rejection_stores_reason_without_payload_values(
    db_session: AsyncSession, patched_session: None
) -> None:
    await record_rejection(
        "task-bad-1",
        "Malformed relay message: badly formed hexadecimal UUID string",
        "",
        "devbutter_quote_received",
        "ana@example.com",
        {"name": "Ana", "password_setup_url": "https://secret"},
    )

    rows = await RejectedMessageRepository(db_session).list()
    assert len(rows) == 1
    assert rows[0].broker_message_id == "task-bad-1"
    assert "UUID" in rows[0].reason
    assert rows[0].raw_project_id is None  # empty string from the producer
    assert rows[0].template_key == "devbutter_quote_received"
    assert rows[0].payload_keys == ["name", "password_setup_url"]


async def test_record_rejection_is_idempotent_per_task_id(
    db_session: AsyncSession, patched_session: None
) -> None:
    for _ in range(2):
        await record_rejection("task-bad-2", "Malformed", "x" * 500, None, None, None)

    rows = await RejectedMessageRepository(db_session).list()
    assert len(rows) == 1
    assert rows[0].raw_project_id == "x" * 200  # untrusted input is clipped
    assert rows[0].payload_keys == []


def test_malformed_task_is_recorded_and_ends_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[tuple[Any, ...]] = []

    async def fake_process(*args: Any, **kwargs: Any) -> None:
        raise MalformedMessageError("Malformed relay message: bad UUID")

    async def fake_record(*args: Any) -> None:
        recorded.append(args)

    monkeypatch.setattr(tasks_module, "process_message_isolated", fake_process)
    monkeypatch.setattr(tasks_module, "record_rejection", fake_record)

    result = process_message_task.apply(
        kwargs={"project_id": "", "template_key": "k", "to": "a@example.com", "payload": {}},
        task_id="task-bad-3",
    )

    assert result.state == "FAILURE"
    assert isinstance(result.result, MalformedMessageError)
    assert recorded == [
        ("task-bad-3", "Malformed relay message: bad UUID", "", "k", "a@example.com", {})
    ]
