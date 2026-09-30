import uuid
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.blob_client import BlobClient
from app.core.email_provider import ProviderResult
from app.messages.models import MessageStatus
from app.messages.repository import MessageRepository
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate, ProjectUpdate
from app.templates_.repository import TemplateRepository
from app.templates_.schemas import TemplateCreate
from app.worker.tasks import MalformedMessageError, process_message


@pytest.fixture(autouse=True)
def patch_session_factory(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> None:
    """The task opens its own session via session_factory(); route it at the
    same in-memory sqlite session the test fixtures use.
    """
    import app.worker.tasks as tasks_module

    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(tasks_module, "session_factory", lambda: _SessionCtx())


async def _seed_project_and_template(db_session: AsyncSession) -> uuid.UUID:
    project = await ProjectRepository(db_session).create(
        ProjectCreate(
            name="Crate Diggr",
            slug="crate-diggr",
            default_sender_email="noreply@devbutter.com",
            default_sender_name="DevButter",
        )
    )
    from app.templates_.service import TemplateService

    await TemplateService(TemplateRepository(db_session)).publish(
        TemplateCreate(
            project_id=project.id,
            key="welcome_email",
            subject_template="Welcome, {{ name }}!",
            html_template="<p>Hi {{ name }}</p>",
        )
    )
    return project.id


async def test_process_message_success_marks_message_sent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.tasks as tasks_module

    project_id = await _seed_project_and_template(db_session)

    fake_provider = AsyncMock()
    fake_provider.send.return_value = ProviderResult(provider_message_id="brevo-1")
    emit_mock = AsyncMock()
    monkeypatch.setattr(tasks_module, "emit_metrics", emit_mock)

    await process_message(
        "task-123", str(project_id), "welcome_email", "test@example.com", {"name": "Marcos"},
        fake_provider,
    )

    messages = await MessageRepository(db_session).list()
    assert len(messages) == 1
    assert messages[0].status == MessageStatus.SENT
    assert messages[0].rendered_subject == "Welcome, Marcos!"
    assert messages[0].provider_message_id == "brevo-1"
    assert messages[0].broker_message_id == "task-123"
    fake_provider.send.assert_awaited_once()
    emitted_names = [metric[0] for call in emit_mock.await_args_list for metric in call.args[0]]
    assert emitted_names == ["MessagesProcessed", "MessagesSent"]


async def test_process_message_provider_failure_marks_message_failed(
    db_session: AsyncSession,
) -> None:
    from app.core.email_provider import EmailSendError

    project_id = await _seed_project_and_template(db_session)

    fake_provider = AsyncMock()
    fake_provider.send.side_effect = EmailSendError("Brevo send failed (401): unauthorized")

    with pytest.raises(EmailSendError):
        await process_message(
            "task-124", str(project_id), "welcome_email", "test@example.com", {"name": "Marcos"},
            fake_provider,
        )

    messages = await MessageRepository(db_session).list()
    assert len(messages) == 1
    assert messages[0].status == MessageStatus.FAILED
    assert messages[0].last_error is not None
    assert "401" in messages[0].last_error


async def test_process_message_malformed_args_raises_before_any_send(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.tasks as tasks_module

    fake_provider = AsyncMock()
    emit_mock = AsyncMock()
    monkeypatch.setattr(tasks_module, "emit_metrics", emit_mock)

    with pytest.raises(MalformedMessageError):
        # missing template_key
        await process_message(
            "task-125", str(uuid.uuid4()), "", "test@example.com", {}, fake_provider
        )

    fake_provider.send.assert_not_awaited()
    emit_mock.assert_awaited_once_with(
        [("MessagesMalformed", 1, "Count")], {"TemplateKey": "unknown"}
    )


class _FakeBlob:
    def __init__(self, public_url: str | None = "https://blob.test/v1/public/nfse/n.pdf"):
        self.public_url = public_url
        self.ids: list[str] = []

    async def get(self, blob_id: str) -> dict:
        self.ids.append(blob_id)
        return {"id": blob_id, "public_url": self.public_url, "visibility": "public"}


async def _seed_nfse_template(db_session: AsyncSession) -> uuid.UUID:
    from app.templates_.service import TemplateService

    project = await ProjectRepository(db_session).create(
        ProjectCreate(
            name="DevButter",
            slug="devbutter-nfse",
            default_sender_email="noreply@devbutter.com",
            default_sender_name="DevButter",
        )
    )
    await TemplateService(TemplateRepository(db_session)).publish(
        TemplateCreate(
            project_id=project.id,
            key="nota_fiscal_emitida",
            subject_template="Nota {{ nfse_number }}",
            html_template="<p>{{ name }} <a href=\"{{ cta_link }}\">{{ cta_text }}</a></p>",
            variables_schema={
                "type": "object",
                "required": ["name", "title", "message", "cta_text", "cta_link", "logo_url"],
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "message": {"type": "string"},
                    "cta_text": {"type": "string"},
                    "cta_link": {"type": "string"},
                    "logo_url": {"type": "string"},
                    "blob_id": {"type": "string"},
                    "nfse_number": {"type": "string"},
                },
            },
        )
    )
    return project.id


async def test_nota_fiscal_resolves_blob_public_url_as_cta(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.tasks as tasks_module

    project_id = await _seed_nfse_template(db_session)
    fake_provider = AsyncMock()
    fake_provider.send.return_value = ProviderResult(provider_message_id="brevo-nfse")
    monkeypatch.setattr(tasks_module, "emit_metrics", AsyncMock())
    fake_blob = _FakeBlob()
    await process_message(
        "task-126",
        str(project_id),
        "nota_fiscal_emitida",
        "cliente@example.com",
        {
            "name": "Ana",
            "title": "Nota emitida",
            "message": "Disponível",
            "cta_text": "Abrir",
            "logo_url": "https://devbutter.com/logo.png",
            "blob_id": "11111111-1111-1111-1111-111111111111",
            "nfse_number": "99",
        },
        fake_provider,
        blob_client=cast(BlobClient, fake_blob),
    )
    html = fake_provider.send.await_args.kwargs["html"]
    assert "https://blob.test/v1/public/nfse/n.pdf" in html
    assert fake_blob.ids == ["11111111-1111-1111-1111-111111111111"]


async def test_duplicate_broker_message_id_does_not_resend(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.tasks as tasks_module

    project_id = await _seed_project_and_template(db_session)
    fake_provider = AsyncMock()
    fake_provider.send.return_value = ProviderResult(provider_message_id="brevo-1")
    monkeypatch.setattr(tasks_module, "emit_metrics", AsyncMock())
    await process_message(
        "task-same", str(project_id), "welcome_email", "test@example.com", {"name": "Ana"},
        fake_provider,
    )
    await process_message(
        "task-same", str(project_id), "welcome_email", "test@example.com", {"name": "Ana"},
        fake_provider,
    )
    assert fake_provider.send.await_count == 1


async def test_inactive_project_skips_send(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.tasks as tasks_module

    project_id = await _seed_project_and_template(db_session)
    project = await ProjectRepository(db_session).get(project_id)
    assert project is not None
    await ProjectRepository(db_session).update(project, ProjectUpdate(is_active=False))
    fake_provider = AsyncMock()
    monkeypatch.setattr(tasks_module, "emit_metrics", AsyncMock())
    await process_message(
        "task-127", str(project_id), "welcome_email", "test@example.com", {"name": "Ana"},
        fake_provider,
    )
    fake_provider.send.assert_not_awaited()


async def test_process_message_isolated_disposes_engine_after_success(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for #29: the asyncpg pool must not outlive the task's event loop."""
    import app.worker.tasks as tasks_module

    project_id = await _seed_project_and_template(db_session)
    fake_provider = AsyncMock()
    fake_provider.send.return_value = ProviderResult(provider_message_id="brevo-1")
    monkeypatch.setattr(tasks_module, "emit_metrics", AsyncMock())
    fake_engine = AsyncMock()
    monkeypatch.setattr(tasks_module, "engine", fake_engine)

    await tasks_module.process_message_isolated(
        "task-iso-1", str(project_id), "welcome_email", "a@example.com", {"name": "A"},
        fake_provider,
    )

    fake_engine.dispose.assert_awaited_once()


async def test_process_message_isolated_disposes_engine_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.worker.tasks as tasks_module

    monkeypatch.setattr(tasks_module, "emit_metrics", AsyncMock())
    fake_engine = AsyncMock()
    monkeypatch.setattr(tasks_module, "engine", fake_engine)

    with pytest.raises(MalformedMessageError):
        await tasks_module.process_message_isolated(
            "task-iso-2", "not-a-uuid", "welcome_email", "a@example.com", {}, AsyncMock()
        )

    fake_engine.dispose.assert_awaited_once()


def test_seed_all_queues_uses_the_booting_consumer_not_a_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for #30: worker_ready must attach queues to its own consumer."""
    from unittest.mock import MagicMock

    import app.worker.queues as queues_module
    from app.settings.config import settings
    from app.worker.celery_app import celery_app

    async def _fake_queue_names() -> set[str]:
        return {"relay-crate-diggr"}

    broadcast = MagicMock()
    monkeypatch.setattr(queues_module, "_active_project_queue_names", _fake_queue_names)
    monkeypatch.setattr(celery_app.control, "add_consumer", broadcast)
    consumer = MagicMock()

    queues_module.seed_all_queues(consumer=consumer)

    attached = {call.args[0] for call in consumer.add_task_queue.call_args_list}
    assert attached == {"relay-crate-diggr", settings.relay_queue_name}
    broadcast.assert_not_called()


def test_seed_all_queues_falls_back_to_broadcast_without_a_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    import app.worker.queues as queues_module
    from app.settings.config import settings
    from app.worker.celery_app import celery_app

    async def _fake_queue_names() -> set[str]:
        return set()

    broadcast = MagicMock()
    monkeypatch.setattr(queues_module, "_active_project_queue_names", _fake_queue_names)
    monkeypatch.setattr(celery_app.control, "add_consumer", broadcast)

    queues_module.seed_all_queues()

    broadcast.assert_called_once_with(settings.relay_queue_name, reply=False)
