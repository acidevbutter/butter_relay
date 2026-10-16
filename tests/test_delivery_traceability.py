"""DVB-188: no duplicate delivery after a mid-send crash, attempt history, limits and
the minimum delivery-health numbers on the dashboard."""

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

import app.worker.tasks as tasks_module
from app.core.email_provider import EmailSendError, ProviderResult
from app.messages.models import MessageStatus
from app.messages.repository import MessageRepository
from app.messages.schemas import MessageCreate
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate
from app.settings.config import settings
from app.templates_.repository import TemplateRepository
from app.templates_.schemas import TemplateCreate
from app.templates_.service import TemplateService
from app.worker.tasks import MalformedMessageError, process_message
from tests.conftest import create_project_and_api_key


@pytest.fixture(autouse=True)
def patch_session_factory(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> None:
    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(tasks_module, "session_factory", lambda: _SessionCtx())


async def _seed(db_session: AsyncSession) -> uuid.UUID:
    project = await ProjectRepository(db_session).create(
        ProjectCreate(
            name="Trace",
            slug="trace",
            default_sender_email="noreply@devbutter.com",
            default_sender_name="DevButter",
        )
    )
    await TemplateService(TemplateRepository(db_session)).publish(
        TemplateCreate(
            project_id=project.id,
            key="welcome_email",
            subject_template="Hi {{ name }}",
            html_template="<p>Hi {{ name }}</p>",
        )
    )
    return project.id


async def test_redelivery_after_a_mid_send_crash_does_not_send_again(
    db_session: AsyncSession,
) -> None:
    """The worker died after mark_processing (maybe after the provider accepted the
    e-mail): the redelivered task must not send a second copy."""
    project_id = await _seed(db_session)
    repo = MessageRepository(db_session)
    message = await repo.create(
        MessageCreate(
            project_id=project_id,
            to_email="ana@example.com",
            broker_message_id="task-crash",
            payload={"name": "Ana"},
        )
    )
    await repo.mark_processing(message, "Hi Ana")
    provider = AsyncMock()

    await process_message(
        "task-crash", str(project_id), "welcome_email", "ana@example.com", {"name": "Ana"},
        provider,
    )

    provider.send.assert_not_awaited()
    refreshed = (await repo.list())[0]
    assert refreshed.status == MessageStatus.FAILED
    assert refreshed.terminal_failure is True
    assert "outcome unknown" in (refreshed.last_error or "")


async def test_every_attempt_is_kept_in_the_history(db_session: AsyncSession) -> None:
    project_id = await _seed(db_session)
    provider = AsyncMock()
    provider.send.side_effect = [
        EmailSendError("Brevo request failed: ConnectError"),
        ProviderResult(provider_message_id="brevo-ok"),
    ]
    args = ("task-history", str(project_id), "welcome_email", "ana@example.com", {"name": "A"})

    with pytest.raises(EmailSendError):
        await process_message(*args, provider)
    await process_message(*args, provider)

    repo = MessageRepository(db_session)
    message = (await repo.list())[0]
    attempts = await repo.list_attempts(message.id)
    assert [(a.number, a.outcome, a.terminal) for a in attempts] == [
        (1, "failed", False),
        (2, "sent", False),
    ]
    assert attempts[0].error == "Brevo request failed: ConnectError"
    assert attempts[1].provider_message_id == "brevo-ok"


async def test_oversized_payload_is_rejected_before_rendering(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await _seed(db_session)
    monkeypatch.setattr(settings, "relay_max_payload_bytes", 100)

    with pytest.raises(MalformedMessageError, match="limit is 100"):
        await process_message(
            "task-big", str(project_id), "welcome_email", "a@example.com", {"name": "x" * 200}
        )
    assert await MessageRepository(db_session).list() == []


async def test_non_object_payload_is_rejected(db_session: AsyncSession) -> None:
    project_id = await _seed(db_session)
    with pytest.raises(MalformedMessageError, match="payload must be an object"):
        await process_message(
            "task-list", str(project_id), "welcome_email", "a@example.com",
            ["not", "a", "dict"],  # type: ignore[arg-type]
        )


def test_template_size_limits() -> None:
    with pytest.raises(ValidationError):
        TemplateCreate(
            project_id=uuid.uuid4(), key="k", subject_template="s" * 501, html_template="<p/>"
        )
    with pytest.raises(ValidationError):
        TemplateCreate(
            project_id=uuid.uuid4(), key="k", subject_template="s",
            html_template="x" * (512 * 1024 + 1),
        )


async def test_message_listing_limit_is_capped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, key = await create_project_and_api_key(db_session)
    headers = {"Authorization": f"Bearer {key}"}

    assert (await client.get("/api/v1/messages/?limit=200", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/messages/?limit=201", headers=headers)).status_code == 422


async def test_dashboard_shows_delivery_health_and_backlog(
    admin_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.admin.router as admin_router

    project_id = await _seed(db_session)
    repo = MessageRepository(db_session)
    message = await repo.create(MessageCreate(project_id=project_id, to_email="a@example.com"))
    await repo.mark_failed(message, "nope", terminal=True)

    async def fake_backlog(queues: list[str]) -> dict[str, int]:
        return {queue: 7 for queue in queues}

    monkeypatch.setattr(admin_router, "queue_backlog", fake_backlog)

    page = await admin_client.get("/admin")

    assert page.status_code == 200
    assert "Delivery health (last 24h)" in page.text
    assert "<th>Failed for good</th><td>1</td>" in page.text
    assert "<code>relay-trace</code>" in page.text
    assert "<code>relay-shared</code>" in page.text
