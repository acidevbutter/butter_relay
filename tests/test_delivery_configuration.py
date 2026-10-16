"""DVB-206: the shared Brevo key is only needed by projects without their own."""

import logging

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.main as main_module
from app.core.email_provider import EmailSendError, get_email_provider_for_project
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectCreate, ProjectSecretsUpdate
from app.projects.service import ProjectService
from app.settings.config import settings


def test_brevo_without_any_key_fails_permanently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "brevo_api_key", "")
    with pytest.raises(EmailSendError, match="No Brevo API key") as info:
        get_email_provider_for_project("brevo", {}, settings, {})
    assert info.value.permanent is True
    # The project's own key is enough.
    get_email_provider_for_project("brevo", {}, settings, {"brevo_api_key": "own"})


async def test_startup_check_lists_projects_relying_on_the_shared_key(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _SessionCtx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(main_module, "session_factory", lambda: _SessionCtx())
    service = ProjectService(ProjectRepository(db_session))
    for slug in ("needs-shared", "has-own", "smtp-one"):
        await service.create(
            ProjectCreate(
                name=slug, slug=slug, default_sender_email="n@devbutter.com",
                default_sender_name="D",
                delivery_provider="smtp" if slug == "smtp-one" else "brevo",
            )
        )
    own = await service.get_by_slug("has-own")
    await service.update_secrets(own.id, ProjectSecretsUpdate(brevo_api_key="own-key"))

    monkeypatch.setattr(settings, "brevo_api_key", "")
    with caplog.at_level(logging.ERROR):
        missing = await main_module.check_delivery_configuration()
    assert missing == ["needs-shared"]
    assert "needs-shared" in caplog.text

    monkeypatch.setattr(settings, "brevo_api_key", "shared")
    assert await main_module.check_delivery_configuration() == []
