import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.email_provider import ProviderResult
from app.messages.models import MessageStatus
from app.messages.repository import MessageRepository
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate
from app.templates_.repository import TemplateRepository
from app.templates_.schemas import TemplateCreate
from app.worker.consumer import MalformedMessageError, process_sqs_message


def _sqs_message(project_id: uuid.UUID, body: dict) -> dict:
    return {
        "MessageId": "sqs-123",
        "Body": json.dumps(body),
        "ReceiptHandle": "receipt-123",
        "MessageAttributes": {"project_id": {"StringValue": str(project_id), "DataType": "String"}},
    }


@pytest.fixture(autouse=True)
def patch_session_factory(monkeypatch, db_session):
    """The worker opens its own session via session_factory(); route it at the
    same in-memory sqlite session the test fixtures use.
    """
    import app.worker.consumer as consumer_module

    class _SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(consumer_module, "session_factory", lambda: _SessionCtx())


async def _seed_project_and_template(db_session) -> uuid.UUID:
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


async def test_process_sqs_message_success_marks_message_sent(db_session):
    project_id = await _seed_project_and_template(db_session)
    sqs_message = _sqs_message(
        project_id,
        {"template_key": "welcome_email", "to": "test@example.com", "payload": {"name": "Marcos"}},
    )

    fake_provider = AsyncMock()
    fake_provider.send.return_value = ProviderResult(provider_message_id="brevo-1")

    await process_sqs_message(sqs_message, fake_provider)

    messages = await MessageRepository(db_session).list()
    assert len(messages) == 1
    assert messages[0].status == MessageStatus.SENT
    assert messages[0].rendered_subject == "Welcome, Marcos!"
    assert messages[0].provider_message_id == "brevo-1"
    fake_provider.send.assert_awaited_once()


async def test_process_sqs_message_provider_failure_marks_message_failed(db_session):
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
    assert "401" in messages[0].last_error


async def test_process_sqs_message_malformed_body_raises_before_any_send(db_session):
    project_id = uuid.uuid4()
    sqs_message = _sqs_message(project_id, {"to": "test@example.com"})  # missing template_key

    fake_provider = AsyncMock()

    with pytest.raises(MalformedMessageError):
        await process_sqs_message(sqs_message, fake_provider)

    fake_provider.send.assert_not_awaited()
