import uuid
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.blob_client import BlobClient
from app.core.email_provider import ProviderResult
from app.core.exceptions import ValidationFailedError
from app.messages.models import MessageStatus
from app.messages.repository import MessageRepository
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate, ProjectUpdate
from app.templates_.repository import TemplateRepository
from app.templates_.schemas import TemplateCreate
from app.worker.tasks import process_message


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
            sqs_queue_url="unused-legacy-field",
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
        project_id=project_id,
        template_key="welcome_email",
        to_email="test@example.com",
        payload={"name": "Marcos"},
        message_id="task-1",
        email_provider=fake_provider,
    )

    messages = await MessageRepository(db_session).list()
    assert len(messages) == 1
    assert messages[0].status == MessageStatus.SENT
    assert messages[0].rendered_subject == "Welcome, Marcos!"
    assert messages[0].provider_message_id == "brevo-1"
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
            project_id=project_id,
            template_key="welcome_email",
            to_email="test@example.com",
            payload={"name": "Marcos"},
            message_id="task-2",
            email_provider=fake_provider,
        )

    messages = await MessageRepository(db_session).list()
    assert len(messages) == 1
    assert messages[0].status == MessageStatus.FAILED
    assert messages[0].last_error is not None
    assert "401" in messages[0].last_error


async def test_process_message_unknown_project_raises_before_any_send(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.tasks as tasks_module
    from app.core.exceptions import NotFoundError

    fake_provider = AsyncMock()
    emit_mock = AsyncMock()
    monkeypatch.setattr(tasks_module, "emit_metrics", emit_mock)

    with pytest.raises(NotFoundError):
        await process_message(
            project_id=uuid.uuid4(),
            template_key="welcome_email",
            to_email="test@example.com",
            payload={},
            message_id="task-3",
            email_provider=fake_provider,
        )

    fake_provider.send.assert_not_awaited()


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
            sqs_queue_url="unused-legacy-field",
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
        project_id=project_id,
        template_key="nota_fiscal_emitida",
        to_email="cliente@example.com",
        payload={
            "name": "Ana",
            "title": "Nota emitida",
            "message": "Disponível",
            "cta_text": "Abrir",
            "logo_url": "https://devbutter.com/logo.png",
            "blob_id": "11111111-1111-1111-1111-111111111111",
            "nfse_number": "99",
        },
        message_id="task-nfse-1",
        email_provider=fake_provider,
        blob_client=cast(BlobClient, fake_blob),
    )

    html = fake_provider.send.await_args.kwargs["html"]
    assert "https://blob.test/v1/public/nfse/n.pdf" in html
    assert fake_blob.ids == ["11111111-1111-1111-1111-111111111111"]


async def test_nota_fiscal_missing_public_url_raises_validation_error(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.tasks as tasks_module

    project_id = await _seed_nfse_template(db_session)
    fake_provider = AsyncMock()
    monkeypatch.setattr(tasks_module, "emit_metrics", AsyncMock())
    fake_blob = _FakeBlob(public_url=None)

    with pytest.raises(ValidationFailedError):
        await process_message(
            project_id=project_id,
            template_key="nota_fiscal_emitida",
            to_email="cliente@example.com",
            payload={
                "name": "Ana",
                "title": "Nota emitida",
                "message": "Disponível",
                "cta_text": "Abrir",
                "logo_url": "https://devbutter.com/logo.png",
                "blob_id": "11111111-1111-1111-1111-111111111111",
                "nfse_number": "99",
            },
            message_id="task-nfse-2",
            email_provider=fake_provider,
            blob_client=cast(BlobClient, fake_blob),
        )

    fake_provider.send.assert_not_awaited()
    messages = await MessageRepository(db_session).list()
    assert len(messages) == 0


async def test_duplicate_message_id_does_not_resend(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.tasks as tasks_module

    project_id = await _seed_project_and_template(db_session)
    fake_provider = AsyncMock()
    fake_provider.send.return_value = ProviderResult(provider_message_id="brevo-1")
    monkeypatch.setattr(tasks_module, "emit_metrics", AsyncMock())

    kwargs = dict(
        project_id=project_id,
        template_key="welcome_email",
        to_email="test@example.com",
        payload={"name": "Ana"},
        message_id="same-task-id",
        email_provider=fake_provider,
    )
    await process_message(**kwargs)
    await process_message(**kwargs)
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
        project_id=project_id,
        template_key="welcome_email",
        to_email="test@example.com",
        payload={"name": "Ana"},
        message_id="task-inactive",
        email_provider=fake_provider,
    )

    fake_provider.send.assert_not_awaited()
