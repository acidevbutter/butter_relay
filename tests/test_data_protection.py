"""DVB-208 / M-05: minimisation of what leaves the database and retention."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.worker.tasks as tasks_module
from app.core.email_provider import BrevoEmailProvider, EmailSendError
from app.core.redaction import mask_email, redact
from app.messages.models import Message, MessageStatus
from app.rejected_messages.models import RejectedMessage
from app.worker.retention import purge_expired_bodies
from tests.conftest import create_project_and_api_key

BREVO_KEY = "xkeysib-" + "a1b2c3d4" * 8


def test_mask_email_and_redact() -> None:
    assert mask_email("ana.souza@example.com") == "a***@example.com"
    assert mask_email("not-an-email") == "***"
    text = f"Key {BREVO_KEY} rejected for ana.souza@example.com " + "x" * 400
    cleaned = redact(text)
    assert cleaned is not None
    assert BREVO_KEY not in cleaned and "[redacted]" in cleaned
    assert "ana.souza@" not in cleaned and "a***@example.com" in cleaned
    assert len(cleaned) <= 300


async def test_brevo_error_keeps_code_and_redacts_body() -> None:
    def reject(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "code": "unauthorized",
                "message": f"Key not found: {BREVO_KEY} (ana@example.com)",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as client:
        provider = BrevoEmailProvider(api_key="test", client=client)
        with pytest.raises(EmailSendError) as info:
            await provider.send(
                to="ana@example.com", subject="s", html="<p/>",
                from_email="noreply@devbutter.com", from_name="DevButter",
            )

    detail = info.value.detail
    assert detail.startswith("Brevo send failed (401): unauthorized:")
    assert BREVO_KEY not in detail
    assert "ana@example.com" not in detail


async def test_message_api_masks_recipient_and_hides_payload(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    project_id, key = await create_project_and_api_key(db_session)
    message = Message(
        project_id=uuid.UUID(project_id),
        to_email="ana.souza@example.com",
        payload={"password_setup_url": "https://app/portal/senha/secret-token"},
        status=MessageStatus.FAILED,
        # A row written before DVB-208 with the raw provider body.
        last_error=f'Brevo send failed (401): {{"message": "{BREVO_KEY}"}}',
    )
    db_session.add(message)
    await db_session.commit()
    await db_session.refresh(message)
    headers = {"Authorization": f"Bearer {key}"}

    listing = (await client.get("/api/v1/messages/", headers=headers)).json()
    detail = (await client.get(f"/api/v1/messages/{message.id}", headers=headers)).json()

    for body in (listing[0], detail):
        assert "payload" not in body
        assert body["to_email"] == "a***@example.com"
        assert BREVO_KEY not in body["last_error"]


async def test_blocked_recipient_log_has_no_full_address(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.projects.repository import ProjectRepository
    from app.projects.schemas import ProjectCreate
    from app.settings.config import settings
    from app.templates_.repository import TemplateRepository
    from app.templates_.schemas import TemplateCreate
    from app.templates_.service import TemplateService

    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(tasks_module, "session_factory", lambda: _SessionCtx())
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "allowed_recipient_domains", "devbutter.com")
    project = await ProjectRepository(db_session).create(
        ProjectCreate(
            name="P", slug="p", default_sender_email="n@devbutter.com", default_sender_name="D"
        )
    )
    await TemplateService(TemplateRepository(db_session)).publish(
        TemplateCreate(project_id=project.id, key="k", subject_template="s", html_template="h")
    )

    with caplog.at_level(logging.WARNING):
        await tasks_module.process_message(
            "task-log", str(project.id), "k", "ana.souza@gmail.com", {}, AsyncMock()
        )

    assert "ana.souza@gmail.com" not in caplog.text
    assert "a***@gmail.com" in caplog.text


async def test_retention_purges_old_payloads_and_rejected_recipients(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.retention as retention_module

    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(retention_module, "session_factory", lambda: _SessionCtx())
    monkeypatch.setattr(retention_module, "engine", AsyncMock())
    project_id, _ = await create_project_and_api_key(db_session)
    old = datetime.now(UTC) - timedelta(days=31)
    rows = {
        "old_sent": Message(project_id=uuid.UUID(project_id), to_email="a@x.com",
                            payload={"k": 1}, status=MessageStatus.SENT, received_at=old),
        "old_pending": Message(project_id=uuid.UUID(project_id), to_email="b@x.com",
                               payload={"k": 1}, status=MessageStatus.PENDING, received_at=old),
        "recent_sent": Message(project_id=uuid.UUID(project_id), to_email="c@x.com",
                               payload={"k": 1}, status=MessageStatus.SENT),
    }
    db_session.add_all(rows.values())
    db_session.add(RejectedMessage(broker_message_id="r-old", reason="bad",
                                   to_email="d@x.com", payload_keys=[], received_at=old))
    await db_session.commit()

    result = await purge_expired_bodies(30)

    assert result == {"messages": 1, "rejected": 1, "retention_days": 30}
    for row in rows.values():
        await db_session.refresh(row)
    assert rows["old_sent"].payload == {}
    assert rows["old_pending"].payload == {"k": 1}
    assert rows["recent_sent"].payload == {"k": 1}


def test_purge_statement_is_valid_postgres_sql() -> None:
    """Regression (#73): `payload != {}` worked in SQLite, but Postgres has no
    `json <> json`; the real statement must compare the payload's text form."""
    from sqlalchemy.dialects import postgresql

    from app.messages.repository import MessageRepository

    stmt = MessageRepository.purge_bodies_statement(datetime.now(UTC))
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "CAST(messages.payload AS TEXT) !=" in sql
