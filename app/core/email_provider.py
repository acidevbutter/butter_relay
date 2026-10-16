"""Email delivery abstraction.

EmailProvider is a Protocol so the worker/service layer never depends on a
specific vendor SDK. Each company (Project) picks its own delivery channel
(`Project.delivery_provider`, edited in the relay admin or through the API):

- `brevo` (default everywhere, including development): Brevo transactional API,
  authenticated with the project's own API key when it has one (encrypted in
  `projects.delivery_secrets_enc`) and the shared BREVO_API_KEY otherwise.
- `smtp`: SMTP with `none` (development only), `starttls` or `ssl`, optional
  username/password -- the mailpit sandbox of the local stack by default
  (SMTP_HOST/SMTP_PORT), or the host stored in the project's delivery_config.

Every provider takes one `OutboundEmail` (to/cc/bcc, reply-to, HTML + text,
headers, attachments) -- DVB-306 / S1.

`get_email_provider_for_project` is the only place that maps a stored provider
name to an implementation; adding a channel means one new class here, one enum
member, and one branch in that factory -- nothing in the worker or services.
"""

from __future__ import annotations

import asyncio
import base64
import enum
import logging
import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any, Protocol

import httpx

from app.core.redaction import redact
from app.settings.config import Settings

logger = logging.getLogger(__name__)

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
DEFAULT_TIMEOUT_SECONDS = 10.0


class DeliveryProvider(str, enum.Enum):
    BREVO = "brevo"
    SMTP = "smtp"


class SmtpSecurity(str, enum.Enum):
    NONE = "none"  # development only (mailpit)
    STARTTLS = "starttls"
    SSL = "ssl"


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class OutboundEmail:
    to: list[str]
    subject: str
    html: str
    from_email: str
    from_name: str | None = None
    text: str | None = None
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    reply_to: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class ProviderResult:
    provider_message_id: str


class EmailSendError(Exception):
    """A provider failure. `permanent` means retrying cannot help (rejected request,
    bad credentials, refused recipient, unknown channel): the worker fails the
    message for good instead of burning the retry budget. Transient failures
    (timeouts, 5xx, 429, connection errors) stay retryable."""

    def __init__(self, detail: str, *, permanent: bool = False):
        super().__init__(detail)
        self.detail = detail
        self.permanent = permanent

    @property
    def retryable(self) -> bool:
        return not self.permanent


# Brevo statuses that no amount of retrying fixes: malformed request, bad/forbidden
# API key, unknown resource, unprocessable content. Everything else (408, 429, 5xx)
# is worth retrying.
_BREVO_PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 422})


class EmailProvider(Protocol):
    async def send(self, email: OutboundEmail) -> ProviderResult: ...


def _brevo_addresses(addresses: list[str]) -> list[dict[str, str]]:
    return [{"email": address} for address in addresses]


class BrevoEmailProvider:
    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._api_key = api_key
        self._client = client
        self._timeout = timeout

    def _payload(self, email: OutboundEmail) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sender": {"email": email.from_email, "name": email.from_name or email.from_email},
            "to": _brevo_addresses(email.to),
            "subject": email.subject,
            "htmlContent": email.html,
        }
        if email.cc:
            payload["cc"] = _brevo_addresses(email.cc)
        if email.bcc:
            payload["bcc"] = _brevo_addresses(email.bcc)
        if email.reply_to:
            payload["replyTo"] = {"email": email.reply_to}
        if email.text:
            payload["textContent"] = email.text
        if email.headers:
            payload["headers"] = dict(email.headers)
        if email.attachments:
            payload["attachment"] = [
                {"name": a.filename, "content": base64.b64encode(a.data).decode()}
                for a in email.attachments
            ]
        return payload

    async def send(self, email: OutboundEmail) -> ProviderResult:
        headers = {"api-key": self._api_key, "content-type": "application/json"}
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(BREVO_SEND_URL, json=self._payload(email), headers=headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "brevo request failed: %s",
                type(exc).__name__,
                extra={"event": "email_provider_error", "provider": "brevo"},
            )
            raise EmailSendError(f"Brevo request failed: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            logger.warning(
                "brevo rejected the send: HTTP %s",
                response.status_code,
                extra={"event": "email_provider_error", "provider": "brevo"},
            )
            raise EmailSendError(
                f"Brevo send failed ({response.status_code}): {_brevo_error_summary(response)}",
                permanent=response.status_code in _BREVO_PERMANENT_STATUSES,
            )

        data = response.json()
        return ProviderResult(provider_message_id=data.get("messageId", ""))


def _brevo_error_summary(response: httpx.Response) -> str:
    """Brevo answers {"code": ..., "message": ...}; keep the code and a redacted,
    truncated message instead of persisting the whole body (DVB-208)."""
    try:
        body = response.json()
    except ValueError:
        return redact(response.text) or ""
    if not isinstance(body, dict):
        return redact(str(body)) or ""
    code = body.get("code")
    message = redact(str(body.get("message") or "")) or ""
    return f"{code}: {message}" if code else message


def _smtp_error_is_permanent(exc: Exception) -> bool:
    """SMTP 5xx replies (550/553 refused recipient, 535 bad credentials, ...) and
    refused senders/recipients are final; 4xx replies and transport errors
    (connection refused, timeouts) are transient."""
    if isinstance(exc, smtplib.SMTPRecipientsRefused | smtplib.SMTPSenderRefused):
        return True
    if isinstance(exc, smtplib.SMTPResponseException):
        return exc.smtp_code >= 500
    return False


def build_mime_message(email: OutboundEmail) -> EmailMessage:
    """multipart/alternative (text + HTML) with attachments, Cc, Reply-To and a
    Message-ID of our own. Bcc is never written as a header."""
    message = EmailMessage()
    message["Subject"] = email.subject
    message["From"] = formataddr((email.from_name or "", email.from_email))
    message["To"] = ", ".join(email.to)
    if email.cc:
        message["Cc"] = ", ".join(email.cc)
    if email.reply_to:
        message["Reply-To"] = email.reply_to
    sender_domain = email.from_email.rpartition("@")[2] or None
    message["Message-ID"] = make_msgid(domain=sender_domain)
    for name, value in email.headers.items():
        message[name] = value
    message.set_content(email.text or "This message requires an HTML-capable e-mail client.")
    message.add_alternative(email.html, subtype="html")
    for attachment in email.attachments:
        maintype, _, subtype = attachment.content_type.partition("/")
        message.add_attachment(
            attachment.data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=attachment.filename,
        )
    return message


class SmtpEmailProvider:
    """SMTP delivery: `none` (plain, development/mailpit only), `starttls` or `ssl`,
    optionally authenticated."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        security: SmtpSecurity = SmtpSecurity.NONE,
        username: str | None = None,
        password: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._host = host
        self._port = port
        self._security = security
        self._username = username
        self._password = password
        self._timeout = timeout

    async def send(self, email: OutboundEmail) -> ProviderResult:
        message = build_mime_message(email)
        recipients = [*email.to, *email.cc, *email.bcc]
        try:
            await asyncio.to_thread(self._send_sync, message, email.from_email, recipients)
        except Exception as exc:
            logger.warning(
                "smtp send failed (%s:%s): %s",
                self._host,
                self._port,
                type(exc).__name__,
                extra={"event": "email_provider_error", "provider": "smtp"},
            )
            raise EmailSendError(
                f"SMTP send failed ({self._host}:{self._port}): {redact(str(exc))}",
                permanent=_smtp_error_is_permanent(exc),
            ) from exc
        return ProviderResult(provider_message_id=str(message["Message-ID"]))

    def _connect(self) -> smtplib.SMTP:
        if self._security is SmtpSecurity.SSL:
            return smtplib.SMTP_SSL(
                self._host, self._port, timeout=self._timeout,
                context=ssl.create_default_context(),
            )
        client = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        if self._security is SmtpSecurity.STARTTLS:
            client.starttls(context=ssl.create_default_context())
        return client

    def _send_sync(self, message: EmailMessage, sender: str, recipients: list[str]) -> None:
        with self._connect() as client:
            if self._username:
                client.login(self._username, self._password or "")
            client.send_message(message, from_addr=sender, to_addrs=recipients)


def get_email_provider_for_project(
    provider: str,
    config: Mapping[str, Any] | None,
    settings: Settings,
    secrets: Mapping[str, Any] | None = None,
) -> EmailProvider:
    """Resolves a project's stored delivery settings (and its decrypted secrets) to a
    provider instance.

    Takes plain values (not the Project ORM object) so core/ never imports a
    domain package. An unknown provider name fails loudly instead of silently
    falling back to another channel.
    """
    config = config or {}
    secrets = secrets or {}
    timeout = float(config.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    if provider == DeliveryProvider.BREVO.value:
        api_key = str(secrets.get("brevo_api_key") or settings.brevo_api_key)
        return BrevoEmailProvider(api_key=api_key, timeout=timeout)
    if provider == DeliveryProvider.SMTP.value:
        default_security = (
            SmtpSecurity.NONE if settings.environment == "development" else SmtpSecurity.STARTTLS
        )
        security = SmtpSecurity(config.get("smtp_security") or default_security)
        if security is SmtpSecurity.NONE and settings.environment != "development":
            raise EmailSendError(
                "SMTP without TLS is only allowed in development; set smtp_security to "
                "starttls or ssl",
                permanent=True,
            )
        return SmtpEmailProvider(
            host=str(config.get("smtp_host") or settings.smtp_host),
            port=int(config.get("smtp_port") or settings.smtp_port),
            security=security,
            username=config.get("smtp_username") or None,
            password=secrets.get("smtp_password") or None,
            timeout=timeout,
        )
    raise EmailSendError(f"Unknown delivery provider {provider!r}", permanent=True)
