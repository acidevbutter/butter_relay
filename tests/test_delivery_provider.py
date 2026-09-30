"""Per-project delivery provider: factory, API contract and worker wiring."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email_provider import (
    BrevoEmailProvider,
    DeliveryProvider,
    EmailSendError,
    ProviderResult,
    SmtpEmailProvider,
    get_email_provider_for_project,
)
from app.messages.repository import MessageRepository
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate, ProjectUpdate
from app.settings.config import settings
from app.templates_.repository import TemplateRepository
from app.templates_.schemas import TemplateCreate
from app.templates_.service import TemplateService
from app.worker.tasks import process_message

PROJECT_PAYLOAD = {
    "name": "Crate Diggr",
    "slug": "crate-diggr",
    "default_sender_email": "noreply@devbutter.com",
    "default_sender_name": "DevButter",
}


# --- factory -------------------------------------------------------------------


def test_factory_returns_brevo_by_default_even_in_development() -> None:
    assert settings.environment == "development"
    provider = get_email_provider_for_project("brevo", {}, settings)
    assert isinstance(provider, BrevoEmailProvider)


def test_factory_smtp_falls_back_to_settings_host_and_port() -> None:
    provider = get_email_provider_for_project("smtp", {}, settings)
    assert isinstance(provider, SmtpEmailProvider)
    assert provider._host == settings.smtp_host
    assert provider._port == settings.smtp_port


def test_factory_smtp_uses_project_host_and_port() -> None:
    provider = get_email_provider_for_project(
        "smtp", {"smtp_host": "smtp.acme.test", "smtp_port": 2525}, settings
    )
    assert isinstance(provider, SmtpEmailProvider)
    assert (provider._host, provider._port) == ("smtp.acme.test", 2525)


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(EmailSendError, match="Unknown delivery provider"):
        get_email_provider_for_project("carrier-pigeon", {}, settings)


async def test_smtp_provider_wraps_connection_errors_as_email_send_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.email_provider as module

    monkeypatch.setattr(module, "emit_metrics", AsyncMock())
    provider = SmtpEmailProvider(host="127.0.0.1", port=1)  # nothing listens on port 1

    with pytest.raises(EmailSendError, match="SMTP send failed"):
        await provider.send(to="a@b.co", subject="s", html="<p>x</p>", from_email="f@b.co")


# --- API contract --------------------------------------------------------------


async def test_new_project_defaults_to_brevo(admin_client) -> None:
    response = await admin_client.post("/api/v1/projects/", json=PROJECT_PAYLOAD)
    assert response.status_code == 201
    assert response.json()["delivery_provider"] == "brevo"
    assert response.json()["delivery_config"] == {}


async def test_create_project_with_smtp_provider_and_config(admin_client) -> None:
    response = await admin_client.post(
        "/api/v1/projects/",
        json={
            **PROJECT_PAYLOAD,
            "delivery_provider": "smtp",
            "delivery_config": {"smtp_host": "smtp.acme.test", "smtp_port": 2525},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["delivery_provider"] == "smtp"
    assert body["delivery_config"] == {"smtp_host": "smtp.acme.test", "smtp_port": 2525}


async def test_patch_switches_provider_and_clears_config(admin_client) -> None:
    created = (
        await admin_client.post(
            "/api/v1/projects/",
            json={
                **PROJECT_PAYLOAD,
                "delivery_provider": "smtp",
                "delivery_config": {"smtp_host": "smtp.acme.test"},
            },
        )
    ).json()

    response = await admin_client.patch(
        f"/api/v1/projects/{created['id']}",
        json={"delivery_provider": "brevo", "delivery_config": {}},
    )

    assert response.status_code == 200
    assert response.json()["delivery_provider"] == "brevo"
    assert response.json()["delivery_config"] == {}


async def test_patch_without_delivery_fields_leaves_them_untouched(admin_client) -> None:
    created = (
        await admin_client.post(
            "/api/v1/projects/", json={**PROJECT_PAYLOAD, "delivery_provider": "smtp"}
        )
    ).json()

    response = await admin_client.patch(f"/api/v1/projects/{created['id']}", json={"name": "New"})

    assert response.json()["name"] == "New"
    assert response.json()["delivery_provider"] == "smtp"


@pytest.mark.parametrize(
    "body",
    [
        {"delivery_provider": "carrier-pigeon"},
        {"delivery_provider": "smtp", "delivery_config": {"smtp_port": 70000}},
        {"delivery_provider": "smtp", "delivery_config": {"api_key": "secret"}},
    ],
)
async def test_invalid_delivery_settings_return_422(admin_client, body) -> None:
    response = await admin_client.post("/api/v1/projects/", json={**PROJECT_PAYLOAD, **body})
    assert response.status_code == 422


# --- worker --------------------------------------------------------------------


@pytest.fixture
def patch_worker_session(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> None:
    import app.worker.tasks as tasks_module

    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(tasks_module, "session_factory", lambda: _SessionCtx())
    monkeypatch.setattr(tasks_module, "emit_metrics", AsyncMock())


async def _seed(
    db_session: AsyncSession,
    delivery_provider: DeliveryProvider = DeliveryProvider.BREVO,
    delivery_config: dict[str, object] | None = None,
) -> uuid.UUID:
    project = await ProjectRepository(db_session).create(
        ProjectCreate.model_validate(
            {
                "name": "Acme",
                "slug": "acme",
                "default_sender_email": "noreply@acme.com",
                "default_sender_name": "Acme",
                "delivery_provider": delivery_provider,
                "delivery_config": delivery_config or {},
            }
        )
    )
    await TemplateService(TemplateRepository(db_session)).publish(
        TemplateCreate(
            project_id=project.id,
            key="hello",
            subject_template="Hi {{ name }}",
            html_template="<p>{{ name }}</p>",
        )
    )
    return project.id


@pytest.mark.usefixtures("patch_worker_session")
async def test_worker_resolves_provider_from_the_project(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.worker.tasks as tasks_module

    project_id = await _seed(
        db_session,
        delivery_provider=DeliveryProvider.SMTP,
        delivery_config={"smtp_host": "smtp.acme.test", "smtp_port": 2525},
    )
    fake_provider = AsyncMock()
    fake_provider.send.return_value = ProviderResult(provider_message_id="smtp-1")
    calls: list[tuple[str, dict[str, object]]] = []

    def factory(provider: str, config: dict[str, object], _settings: object) -> AsyncMock:
        calls.append((provider, dict(config)))
        return fake_provider

    monkeypatch.setattr(tasks_module, "get_email_provider_for_project", factory)

    await process_message("task-1", str(project_id), "hello", "to@example.com", {"name": "Ana"})

    assert calls == [("smtp", {"smtp_host": "smtp.acme.test", "smtp_port": 2525})]
    fake_provider.send.assert_awaited_once()
    message = (await MessageRepository(db_session).list())[0]
    assert message.provider == "smtp"
    assert message.status == "sent"


@pytest.mark.usefixtures("patch_worker_session")
async def test_worker_message_records_brevo_for_default_projects(
    db_session: AsyncSession,
) -> None:
    project_id = await _seed(db_session)
    fake_provider = AsyncMock()
    fake_provider.send.return_value = ProviderResult(provider_message_id="b-1")

    await process_message(
        "task-2", str(project_id), "hello", "to@example.com", {"name": "Ana"}, fake_provider
    )

    assert (await MessageRepository(db_session).list())[0].provider == "brevo"


async def test_project_update_schema_only_sets_provided_fields() -> None:
    assert ProjectUpdate(delivery_provider=DeliveryProvider.SMTP).model_dump(
        exclude_unset=True
    ) == {"delivery_provider": DeliveryProvider.SMTP}
