"""DVB-309 / S4 (+ DVB-204): POST /api/v1/messages/send and the stored-message worker."""

import base64
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.messages.service as message_service_module
import app.worker.tasks as tasks_module
from app.api_keys.repository import ApiKeyRepository
from app.api_keys.service import ApiKeyService
from app.core.email_provider import OutboundEmail, ProviderResult
from app.email_types.repository import EmailTypeRepository
from app.email_types.schemas import EmailTypeCreate, EmailTypeMode
from app.email_types.service import EmailTypeService
from app.messages.models import MessageStatus
from app.messages.repository import MessageRepository
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectUpdate
from app.projects.service import ProjectService
from app.settings.config import settings
from app.templates_.repository import TemplateRepository
from app.templates_.schemas import TemplateCreate
from app.templates_.service import TemplateService
from app.worker.tasks import MalformedMessageError, process_message_task, process_stored_message
from tests.conftest import create_project_and_api_key


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, str]]:
    calls: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        message_service_module, "enqueue_message", lambda mid, queue: calls.append((mid, queue))
    )
    return calls


@pytest.fixture(autouse=True)
def worker_session(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> None:
    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(tasks_module, "session_factory", lambda: _SessionCtx())


async def _client_setup(db: AsyncSession) -> tuple[uuid.UUID, str]:
    project_id, key = await create_project_and_api_key(db, external=True)
    pid = uuid.UUID(project_id)
    types = EmailTypeService(EmailTypeRepository(db))
    await types.upsert(pid, EmailTypeCreate(key="order.processing", mode=EmailTypeMode.RAW))
    await types.upsert(pid, EmailTypeCreate(key="welcome", mode=EmailTypeMode.TEMPLATE))
    await TemplateService(TemplateRepository(db)).publish(
        TemplateCreate(
            project_id=pid, key="welcome", subject_template="Oi {{ name }}",
            html_template="<p>Oi {{ name }}</p>",
            variables_schema={"type": "object", "required": ["name"]},
        )
    )
    return pid, key


def _auth(key: str, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {key}"}
    if idem:
        headers["Idempotency-Key"] = idem
    return headers


RAW = {
    "type": "order.processing",
    "to": ["cliente@example.com"],
    "cc": ["copia@example.com"],
    "bcc": ["arquivo@example.com"],
    "reply_to": "contato@acme.com",
    "subject": "Seu pedido #123",
    "html": "<p>Recebemos seu pedido</p>",
    "text": "Recebemos seu pedido",
    "headers": {"X-Entity-Ref-ID": "123"},
    "attachments": [
        {"filename": "nota.pdf", "content_type": "application/pdf",
         "content_base64": base64.b64encode(b"%PDF-1.4").decode()}
    ],
    "metadata": {"source": "wp-plugin/0.1.0", "site": "acme.com"},
}


BIG_ATTACHMENT = base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode()

# --- API ------------------------------------------------------------------------


async def test_raw_send_is_stored_then_queued_on_the_project_queue(
    client: AsyncClient, db_session: AsyncSession, queued: list[tuple[uuid.UUID, str]]
) -> None:
    pid, key = await _client_setup(db_session)

    response = await client.post("/api/v1/messages/send", headers=_auth(key), json=RAW)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "pending" and body["type"] == "order.processing"
    message_id = uuid.UUID(body["message_id"])
    project = await ProjectService(ProjectRepository(db_session)).get(pid)
    assert queued == [(message_id, project.queue_name)]
    repo = MessageRepository(db_session)
    message = await repo.get_by_id(message_id)
    assert message is not None and message.project_id == pid
    assert message.recipients == {
        "to": ["cliente@example.com"], "cc": ["copia@example.com"], "bcc": ["arquivo@example.com"],
    }
    assert message.source == "wp-plugin/0.1.0" and message.broker_message_id == str(message_id)
    assert [a.filename for a in await repo.list_attachments(message_id)] == ["nota.pdf"]


async def test_send_requires_the_send_scope_and_a_key(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any]
) -> None:
    pid, _ = await _client_setup(db_session)
    _, read_only = await ApiKeyService(
        ApiKeyRepository(db_session), ProjectRepository(db_session)
    ).create(pid, "read", scopes=["read"])

    assert (await client.post("/api/v1/messages/send", json=RAW)).status_code == 401
    denied = await client.post("/api/v1/messages/send", headers=_auth(read_only), json=RAW)
    assert denied.status_code == 403 and queued == []


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        ({"type": "undeclared"}, 422, "unknown_email_type"),
        ({"from": "spoof@evil.com"}, 422, None),
        ({"to": [f"a{i}@example.com" for i in range(51)]}, 422, None),
        ({"html": "x" * (1024 * 1024 + 1)}, 413, "payload_too_large"),
        ({"attachments": [{"filename": "big.bin", "content_base64": BIG_ATTACHMENT}]},
         413, "payload_too_large"),
        ({"attachments": [{"filename": "bad", "content_base64": "%%%"}]},
         422, "invalid_attachment"),
    ],
)
async def test_send_validation_and_limits(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any],
    change: dict[str, Any], status: int, code: str | None,
) -> None:
    _, key = await _client_setup(db_session)
    response = await client.post(
        "/api/v1/messages/send", headers=_auth(key), json={**RAW, **change}
    )
    assert response.status_code == status, response.text
    if code:
        assert response.json()["code"] == code
    assert queued == []


async def test_body_over_3mb_is_413(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any]
) -> None:
    _, key = await _client_setup(db_session)
    response = await client.post(
        "/api/v1/messages/send", headers={**_auth(key), "content-type": "application/json"},
        content=b"{" + b" " * (3 * 1024 * 1024) + b"}",
    )
    assert response.status_code == 413


async def test_template_mode_validates_payload_against_schema(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any]
) -> None:
    _, key = await _client_setup(db_session)
    ok = await client.post(
        "/api/v1/messages/send", headers=_auth(key),
        json={"type": "welcome", "to": ["ana@example.com"], "payload": {"name": "Ana"}},
    )
    bad = await client.post(
        "/api/v1/messages/send", headers=_auth(key),
        json={"type": "welcome", "to": ["ana@example.com"], "payload": {}},
    )
    assert ok.status_code == 202 and bad.status_code == 422
    assert len(queued) == 1


async def test_undeclared_type_allowed_when_the_project_says_so(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any]
) -> None:
    pid, key = await _client_setup(db_session)
    await ProjectService(ProjectRepository(db_session)).update(
        pid, ProjectUpdate(allow_undeclared_types=True)
    )
    response = await client.post(
        "/api/v1/messages/send", headers=_auth(key), json={**RAW, "type": "anything.else"}
    )
    assert response.status_code == 202


async def test_idempotency_key(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any]
) -> None:
    _, key = await _client_setup(db_session)
    first = await client.post("/api/v1/messages/send", headers=_auth(key, "order-123"), json=RAW)
    again = await client.post("/api/v1/messages/send", headers=_auth(key, "order-123"), json=RAW)
    other = await client.post(
        "/api/v1/messages/send", headers=_auth(key, "order-123"),
        json={**RAW, "subject": "Outro assunto"},
    )

    assert first.status_code == 202 and again.status_code == 200
    assert first.json()["message_id"] == again.json()["message_id"]
    assert other.status_code == 409
    assert len(queued) == 1


async def test_enqueue_failure_is_503_and_a_retry_requeues_the_same_message(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, key = await _client_setup(db_session)

    def broker_down(mid: uuid.UUID, queue: str) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(message_service_module, "enqueue_message", broker_down)
    failed = await client.post("/api/v1/messages/send", headers=_auth(key, "k1"), json=RAW)
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(
        message_service_module, "enqueue_message", lambda mid, queue: calls.append(mid)
    )
    retried = await client.post("/api/v1/messages/send", headers=_auth(key, "k1"), json=RAW)

    assert failed.status_code == 503 and failed.json()["code"] == "enqueue_failed"
    assert int(failed.headers["Retry-After"]) > 0
    assert retried.status_code == 202
    assert [str(c) for c in calls] == [retried.json()["message_id"]]
    message = await MessageRepository(db_session).get_by_id(calls[0])
    assert message is not None and message.status == MessageStatus.PENDING


# --- worker -----------------------------------------------------------------------


async def _stored(client: AsyncClient, key: str, body: dict[str, Any]) -> uuid.UUID:
    response = await client.post("/api/v1/messages/send", headers=_auth(key), json=body)
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["message_id"])


async def test_worker_sends_raw_as_is_with_everything(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any]
) -> None:
    pid, key = await _client_setup(db_session)
    message_id = await _stored(client, key, RAW)
    provider = AsyncMock()
    provider.send.return_value = ProviderResult(provider_message_id="brevo-1")
    project = await ProjectService(ProjectRepository(db_session)).get(pid)

    await process_stored_message(str(message_id), project.queue_name, email_provider=provider)

    email: OutboundEmail = provider.send.await_args.args[0]
    assert email.to == ["cliente@example.com"] and email.cc == ["copia@example.com"]
    assert email.bcc == ["arquivo@example.com"] and email.reply_to == "contato@acme.com"
    assert email.subject == "Seu pedido #123" and email.html == "<p>Recebemos seu pedido</p>"
    assert email.text == "Recebemos seu pedido" and email.headers == {"X-Entity-Ref-ID": "123"}
    assert [(a.filename, a.data) for a in email.attachments] == [("nota.pdf", b"%PDF-1.4")]
    assert email.from_email == project.default_sender_email
    repo = MessageRepository(db_session)
    message = await repo.get_by_id(message_id)
    assert message is not None and message.status == MessageStatus.SENT
    assert await repo.list_attachments(message_id) == []  # purged once delivered


async def test_worker_renders_template_messages(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any]
) -> None:
    _, key = await _client_setup(db_session)
    message_id = await _stored(
        client, key, {"type": "welcome", "to": ["ana@example.com"], "payload": {"name": "Ana"}}
    )
    provider = AsyncMock()
    provider.send.return_value = ProviderResult(provider_message_id="b")

    await process_stored_message(str(message_id), None, email_provider=provider)

    email: OutboundEmail = provider.send.await_args.args[0]
    assert (email.subject, email.html) == ("Oi Ana", "<p>Oi Ana</p>")


async def test_worker_refuses_a_task_from_another_projects_queue(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any]
) -> None:
    _, key = await _client_setup(db_session)
    message_id = await _stored(client, key, RAW)
    provider = AsyncMock()

    await process_stored_message(str(message_id), "relay-someone-else", email_provider=provider)

    provider.send.assert_not_awaited()
    message = await MessageRepository(db_session).get_by_id(message_id)
    assert message is not None and message.terminal_failure is True
    assert "relay-someone-else" in (message.last_error or "")


async def test_worker_checks_every_recipient_outside_production(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, key = await _client_setup(db_session)
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "allowed_recipient_domains", "example.com")
    message_id = await _stored(client, key, {**RAW, "cc": ["chefe@gmail.com"]})
    provider = AsyncMock()

    await process_stored_message(str(message_id), None, email_provider=provider)

    provider.send.assert_not_awaited()
    message = await MessageRepository(db_session).get_by_id(message_id)
    assert message is not None and message.terminal_failure is True
    assert "c***@gmail.com" in (message.last_error or "")


async def test_inactive_project_fails_visibly(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any]
) -> None:
    pid, key = await _client_setup(db_session)
    message_id = await _stored(client, key, RAW)
    await ProjectService(ProjectRepository(db_session)).update(pid, ProjectUpdate(is_active=False))

    await process_stored_message(str(message_id), None, email_provider=AsyncMock())

    message = await MessageRepository(db_session).get_by_id(message_id)
    assert message is not None and message.last_error == "project is inactive"


def test_legacy_task_can_be_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """DVB-204: with ALLOW_LEGACY_SEND_TASK=false a producer-supplied project_id is no
    longer enough to send."""
    recorded: list[tuple[Any, ...]] = []

    async def fake_record(*args: Any) -> None:
        recorded.append(args)

    async def must_not_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("legacy path ran")

    monkeypatch.setattr(settings, "allow_legacy_send_task", False)
    monkeypatch.setattr(tasks_module, "record_rejection", fake_record)
    monkeypatch.setattr(tasks_module, "process_message_isolated", must_not_run)

    result = process_message_task.apply(
        kwargs={"project_id": str(uuid.uuid4()), "template_key": "k", "to": "a@example.com"},
        task_id="legacy-1",
    )

    assert result.state == "FAILURE" and isinstance(result.result, MalformedMessageError)
    assert "ALLOW_LEGACY_SEND_TASK" in recorded[0][1]


def test_message_id_task_routes_to_the_stored_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []

    async def fake_stored(*args: Any, **kwargs: Any) -> None:
        seen.append(args)

    monkeypatch.setattr(tasks_module, "process_stored_message_isolated", fake_stored)
    monkeypatch.setattr(settings, "allow_legacy_send_task", False)  # does not affect it

    result = process_message_task.apply(kwargs={"message_id": "abc"}, task_id="abc")

    assert result.state == "SUCCESS" and seen[0][0] == "abc"


# --- admin hosts ------------------------------------------------------------------


async def test_admin_hosts_hide_admin_on_the_public_host(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_allowed_hosts", "relay-admin.devbutter.tech")

    public = {"host": "relay.devbutter.tech"}
    public_admin = await client.get("/admin/login", headers=public)
    public_projects = await client.get("/api/v1/projects/", headers=public)
    public_health = await client.get("/health", headers=public)
    public_send = await client.post("/api/v1/messages/send", headers=public)
    admin_host = await client.get("/admin/login", headers={"host": "relay-admin.devbutter.tech"})

    assert public_admin.status_code == 404 and public_projects.status_code == 404
    assert public_health.status_code == 200
    assert public_send.status_code == 401  # reaches the API (no key)
    assert admin_host.status_code == 200
    assert "access-control-allow-origin" not in public_send.headers


async def test_retention_clears_raw_bodies_and_attachments(
    client: AsyncClient, db_session: AsyncSession, queued: list[Any]
) -> None:
    from datetime import UTC, datetime, timedelta

    _, key = await _client_setup(db_session)
    message_id = await _stored(client, key, RAW)
    repo = MessageRepository(db_session)
    message = await repo.get_by_id(message_id)
    assert message is not None
    message.received_at = datetime.now(UTC) - timedelta(days=40)
    await repo.mark_failed(message, "nope", terminal=True)

    purged = await repo.purge_bodies(datetime.now(UTC) - timedelta(days=30))

    await db_session.refresh(message)
    assert purged == 1
    assert message.raw_html is None and message.raw_text is None
    assert message.raw_subject == "Seu pedido #123"  # the log keeps what was sent
    assert await repo.list_attachments(message_id) == []
