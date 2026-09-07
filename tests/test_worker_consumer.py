import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email_provider import ProviderResult
from app.messages.models import MessageStatus
from app.messages.repository import MessageRepository
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate, ProjectUpdate
from app.templates_.repository import TemplateRepository
from app.templates_.schemas import TemplateCreate
from app.worker.consumer import MalformedMessageError, process_sqs_message


def _sqs_message(project_id: uuid.UUID, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "MessageId": "sqs-123",
        "Body": json.dumps(body),
        "ReceiptHandle": "receipt-123",
        "MessageAttributes": {"project_id": {"StringValue": str(project_id), "DataType": "String"}},
    }


@pytest.fixture(autouse=True)
def patch_session_factory(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> None:
    """The worker opens its own session via session_factory(); route it at the
    same in-memory sqlite session the test fixtures use.
    """
    import app.worker.consumer as consumer_module

    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(consumer_module, "session_factory", lambda: _SessionCtx())


async def _seed_project_and_template(db_session: AsyncSession) -> uuid.UUID:
    project = await ProjectRepository(db_session).create(
        ProjectCreate(
            name="Crate Diggr",
            slug="crate-diggr",
            sqs_queue_url="http://elasticmq:9324/000000000000/relay-shared",
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


async def test_process_sqs_message_success_marks_message_sent(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.consumer as consumer_module

    project_id = await _seed_project_and_template(db_session)
    sqs_message = _sqs_message(
        project_id,
        {"template_key": "welcome_email", "to": "test@example.com", "payload": {"name": "Marcos"}},
    )

    fake_provider = AsyncMock()
    fake_provider.send.return_value = ProviderResult(provider_message_id="brevo-1")
    emit_mock = AsyncMock()
    monkeypatch.setattr(consumer_module, "emit_metrics", emit_mock)

    await process_sqs_message(sqs_message, fake_provider)

    messages = await MessageRepository(db_session).list()
    assert len(messages) == 1
    assert messages[0].status == MessageStatus.SENT
    assert messages[0].rendered_subject == "Welcome, Marcos!"
    assert messages[0].provider_message_id == "brevo-1"
    fake_provider.send.assert_awaited_once()
    emitted_names = [metric[0] for call in emit_mock.await_args_list for metric in call.args[0]]
    assert emitted_names == ["MessagesProcessed", "MessagesSent"]


async def test_process_sqs_message_provider_failure_marks_message_failed(
    db_session: AsyncSession,
) -> None:
    from app.core.email_provider import EmailSendError

    project_id = await _seed_project_and_template(db_session)
    sqs_message = _sqs_message(
        project_id,
        {"template_key": "welcome_email", "to": "test@example.com", "payload": {"name": "Marcos"}},
    )

    fake_provider = AsyncMock()
    fake_provider.send.side_effect = EmailSendError("Brevo send failed (401): unauthorized")

    with pytest.raises(EmailSendError):
        await process_sqs_message(sqs_message, fake_provider)

    messages = await MessageRepository(db_session).list()
    assert len(messages) == 1
    assert messages[0].status == MessageStatus.FAILED
    assert messages[0].last_error is not None
    assert "401" in messages[0].last_error


async def test_process_sqs_message_malformed_body_raises_before_any_send(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.consumer as consumer_module

    project_id = uuid.uuid4()
    sqs_message = _sqs_message(project_id, {"to": "test@example.com"})  # missing template_key

    fake_provider = AsyncMock()
    emit_mock = AsyncMock()
    monkeypatch.setattr(consumer_module, "emit_metrics", emit_mock)

    with pytest.raises(MalformedMessageError):
        await process_sqs_message(sqs_message, fake_provider)

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
            sqs_queue_url="http://elasticmq:9324/000000000000/relay-shared",
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
    import app.worker.consumer as consumer_module

    project_id = await _seed_nfse_template(db_session)
    sqs_message = _sqs_message(
        project_id,
        {
            "template_key": "nota_fiscal_emitida",
            "to": "cliente@example.com",
            "payload": {
                "name": "Ana",
                "title": "Nota emitida",
                "message": "Disponível",
                "cta_text": "Abrir",
                "logo_url": "https://devbutter.com/logo.png",
                "blob_id": "11111111-1111-1111-1111-111111111111",
                "nfse_number": "99",
            },
        },
    )
    fake_provider = AsyncMock()
    fake_provider.send.return_value = ProviderResult(provider_message_id="brevo-nfse")
    monkeypatch.setattr(consumer_module, "emit_metrics", AsyncMock())
    fake_blob = _FakeBlob()
    await process_sqs_message(sqs_message, fake_provider, blob_client=fake_blob)
    html = fake_provider.send.await_args.kwargs["html"]
    assert "https://blob.test/v1/public/nfse/n.pdf" in html
    assert fake_blob.ids == ["11111111-1111-1111-1111-111111111111"]


async def test_duplicate_sqs_message_id_does_not_resend(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.consumer as consumer_module

    project_id = await _seed_project_and_template(db_session)
    sqs_message = _sqs_message(
        project_id,
        {"template_key": "welcome_email", "to": "test@example.com", "payload": {"name": "Ana"}},
    )
    fake_provider = AsyncMock()
    fake_provider.send.return_value = ProviderResult(provider_message_id="brevo-1")
    monkeypatch.setattr(consumer_module, "emit_metrics", AsyncMock())
    await process_sqs_message(sqs_message, fake_provider)
    await process_sqs_message(sqs_message, fake_provider)
    assert fake_provider.send.await_count == 1


async def test_inactive_project_skips_send(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.consumer as consumer_module

    project_id = await _seed_project_and_template(db_session)
    project = await ProjectRepository(db_session).get(project_id)
    assert project is not None
    await ProjectRepository(db_session).update(project, ProjectUpdate(is_active=False))
    sqs_message = _sqs_message(
        project_id,
        {"template_key": "welcome_email", "to": "test@example.com", "payload": {"name": "Ana"}},
    )
    fake_provider = AsyncMock()
    monkeypatch.setattr(consumer_module, "emit_metrics", AsyncMock())
    await process_sqs_message(sqs_message, fake_provider)
    fake_provider.send.assert_not_awaited()

