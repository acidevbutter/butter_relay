"""DVB-306 / S1: full OutboundEmail, SMTP TLS/auth, encrypted per-project secrets."""

import json
import smtplib
from email.message import EmailMessage
from typing import Any, Literal

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email_provider import (
    Attachment,
    BrevoEmailProvider,
    EmailSendError,
    OutboundEmail,
    SmtpEmailProvider,
    SmtpSecurity,
    build_mime_message,
    get_email_provider_for_project,
)
from app.core.secrets import (
    SecretsKeyError,
    decrypt_secrets,
    encrypt_secrets,
    rotate_secrets,
)
from app.projects.repository import ProjectRepository
from app.projects.service import ProjectService
from app.settings.config import Settings, settings

FULL = OutboundEmail(
    to=["ana@example.com", "bia@example.com"],
    cc=["chefe@example.com"],
    bcc=["arquivo@example.com"],
    reply_to="contato@acme.com",
    subject="Pedido #123",
    html="<p>Olá</p>",
    text="Olá",
    headers={"X-Entity-Ref-ID": "123"},
    attachments=[Attachment("nota.pdf", "application/pdf", b"%PDF-1.4")],
    from_email="no-reply@acme.com",
    from_name="Acme",
)


# --- Brevo ----------------------------------------------------------------------


async def test_brevo_sends_every_outbound_field() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        captured["api_key"] = request.headers["api-key"]
        return httpx.Response(201, json={"messageId": "<m1@brevo>"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BrevoEmailProvider("project-key", client=client).send(FULL)

    assert result.provider_message_id == "<m1@brevo>"
    assert captured["api_key"] == "project-key"
    assert captured["to"] == [{"email": "ana@example.com"}, {"email": "bia@example.com"}]
    assert captured["cc"] == [{"email": "chefe@example.com"}]
    assert captured["bcc"] == [{"email": "arquivo@example.com"}]
    assert captured["replyTo"] == {"email": "contato@acme.com"}
    assert captured["textContent"] == "Olá"
    assert captured["headers"] == {"X-Entity-Ref-ID": "123"}
    assert captured["attachment"] == [{"name": "nota.pdf", "content": "JVBERi0xLjQ="}]


def test_factory_prefers_the_project_brevo_key() -> None:
    own = get_email_provider_for_project("brevo", {}, settings, {"brevo_api_key": "own"})
    shared = get_email_provider_for_project("brevo", {}, settings, {})
    assert isinstance(own, BrevoEmailProvider) and own._api_key == "own"
    assert isinstance(shared, BrevoEmailProvider) and shared._api_key == settings.brevo_api_key


# --- SMTP -----------------------------------------------------------------------


def test_mime_message_is_alternative_with_attachment_and_no_bcc_header() -> None:
    message = build_mime_message(FULL)

    assert message["To"] == "ana@example.com, bia@example.com"
    assert message["Cc"] == "chefe@example.com"
    assert message["Bcc"] is None
    assert message["Reply-To"] == "contato@acme.com"
    assert message["Message-ID"].endswith("@acme.com>")
    assert message["X-Entity-Ref-ID"] == "123"
    parts = [p.get_content_type() for p in message.walk()]
    assert "text/plain" in parts and "text/html" in parts and "application/pdf" in parts


class _FakeSmtp:
    instances: list["_FakeSmtp"] = []

    def __init__(self, host: str, port: int, timeout: float = 0, context: Any = None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.sent: tuple[str, list[str]] | None = None
        _FakeSmtp.instances.append(self)

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self, context: Any = None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}")

    def send_message(self, message: EmailMessage, from_addr: str, to_addrs: list[str]) -> None:
        self.sent = (from_addr, to_addrs)


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSmtp]:
    _FakeSmtp.instances = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSmtp)
    return _FakeSmtp


async def test_smtp_starttls_with_login_sends_to_every_recipient(
    fake_smtp: type[_FakeSmtp],
) -> None:
    provider = SmtpEmailProvider(
        "smtp.acme.test", 587, security=SmtpSecurity.STARTTLS,
        username="mailer", password="s3cret", timeout=7,
    )
    result = await provider.send(FULL)

    smtp = fake_smtp.instances[0]
    assert smtp.calls == ["starttls", "login:mailer"]
    assert smtp.timeout == 7
    assert smtp.sent == (
        "no-reply@acme.com",
        ["ana@example.com", "bia@example.com", "chefe@example.com", "arquivo@example.com"],
    )
    assert result.provider_message_id.endswith("@acme.com>")


async def test_smtp_ssl_uses_implicit_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[str] = []

    class _Plain(_FakeSmtp):
        def __init__(self, *a: Any, **k: Any) -> None:
            used.append("plain")
            super().__init__(*a, **k)

    class _Ssl(_FakeSmtp):
        def __init__(self, *a: Any, **k: Any) -> None:
            used.append("ssl")
            super().__init__(*a, **k)

    monkeypatch.setattr(smtplib, "SMTP", _Plain)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _Ssl)

    await SmtpEmailProvider("smtp.acme.test", 465, security=SmtpSecurity.SSL).send(FULL)
    assert used == ["ssl"]


@pytest.mark.parametrize(
    ("error", "permanent"),
    [
        (smtplib.SMTPAuthenticationError(535, b"bad credentials"), True),
        (smtplib.SMTPRecipientsRefused({"a@x.com": (550, b"no such user")}), True),
        (smtplib.SMTPResponseException(451, b"try later"), False),
        (TimeoutError("timed out"), False),
    ],
)
async def test_smtp_errors_are_classified(
    monkeypatch: pytest.MonkeyPatch, error: Exception, permanent: bool
) -> None:
    class _Failing(_FakeSmtp):
        def send_message(self, *a: Any, **k: Any) -> None:
            raise error

    monkeypatch.setattr(smtplib, "SMTP", _Failing)
    with pytest.raises(EmailSendError) as info:
        await SmtpEmailProvider("smtp.acme.test", 25).send(FULL)
    assert info.value.permanent is permanent
    assert info.value.retryable is (not permanent)


def test_plain_smtp_is_refused_outside_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "staging")
    with pytest.raises(EmailSendError, match="only allowed in development") as info:
        get_email_provider_for_project("smtp", {"smtp_security": "none"}, settings)
    assert info.value.permanent is True
    # Outside development the default is STARTTLS.
    provider = get_email_provider_for_project("smtp", {"smtp_host": "h"}, settings)
    assert isinstance(provider, SmtpEmailProvider)
    assert provider._security is SmtpSecurity.STARTTLS


def test_factory_passes_smtp_credentials_from_config_and_secrets() -> None:
    provider = get_email_provider_for_project(
        "smtp",
        {"smtp_host": "h", "smtp_port": 465, "smtp_security": "ssl",
         "smtp_username": "mailer", "timeout_seconds": 5},
        settings,
        {"smtp_password": "s3cret"},
    )
    assert isinstance(provider, SmtpEmailProvider)
    assert (provider._security, provider._username, provider._password, provider._timeout) == (
        SmtpSecurity.SSL, "mailer", "s3cret", 5.0,
    )


# --- secrets ----------------------------------------------------------------------


def _settings(
    keys: str, environment: Literal["development", "production"] = "development"
) -> Settings:
    return Settings.model_construct(relay_secrets_keys=keys, environment=environment)


def test_secrets_roundtrip_and_rotation() -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    token = encrypt_secrets({"smtp_password": "s3cret", "ignored": "x"}, _settings(old))
    assert token is not None and "s3cret" not in token

    rotated_settings = _settings(f"{new},{old}")
    assert decrypt_secrets(token, rotated_settings) == {"smtp_password": "s3cret"}
    rotated = rotate_secrets(token, rotated_settings)
    assert decrypt_secrets(rotated, _settings(new)) == {"smtp_password": "s3cret"}
    with pytest.raises(SecretsKeyError):
        decrypt_secrets(token, _settings(new))


def test_secrets_keys_are_mandatory_outside_development() -> None:
    with pytest.raises(SecretsKeyError, match="RELAY_SECRETS_KEYS"):
        encrypt_secrets({"smtp_password": "x"}, _settings("", environment="production"))


async def test_admin_secrets_are_write_only(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    from tests.test_admin import _create_project, _project_id

    await _create_project(admin_client)
    project_id = await _project_id(db_session)

    page = await admin_client.post(
        f"/admin/projects/{project_id}/secrets",
        data={"smtp_password": "smtp-s3cret", "brevo_api_key": "xkeysib-project"},
    )
    api = await admin_client.get(f"/api/v1/projects/{project_id}")

    assert page.status_code == 200 and "Secrets saved." in page.text
    assert "smtp-s3cret" not in page.text and "xkeysib-project" not in page.text
    body = api.json()
    assert body["has_smtp_password"] is True and body["has_brevo_api_key"] is True
    assert "smtp-s3cret" not in api.text and "delivery_secrets_enc" not in body

    project = await ProjectRepository(db_session).get(project_id)
    assert project is not None
    assert "smtp-s3cret" not in (project.delivery_secrets_enc or "")
    service = ProjectService(ProjectRepository(db_session))
    assert service.delivery_secrets(project) == {
        "smtp_password": "smtp-s3cret", "brevo_api_key": "xkeysib-project",
    }
    provider = service.provider_for(project)
    assert isinstance(provider, BrevoEmailProvider) and provider._api_key == "xkeysib-project"

    # Blank keeps, `clear` removes.
    await admin_client.post(
        f"/admin/projects/{project_id}/secrets", data={"clear": "brevo_api_key"}
    )
    await db_session.refresh(project)
    assert service.delivery_secrets(project) == {"smtp_password": "smtp-s3cret"}
