"""Email delivery abstraction.

EmailProvider is a Protocol so the worker/service layer never depends on a
specific vendor SDK. Each company (Project) picks its own delivery channel
(`Project.delivery_provider`, edited in the relay admin or through the API):

- `brevo` (default everywhere, including development): Brevo transactional API,
  authenticated with the shared BREVO_API_KEY.
- `smtp`: plain SMTP -- the mailpit sandbox of the local stack by default
  (SMTP_HOST/SMTP_PORT), or a host/port stored in the project's delivery_config.

`get_email_provider_for_project` is the only place that maps a stored provider
name to an implementation; adding a channel means one new class here, one enum
member, and one branch in that factory -- nothing in the worker or services.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import smtplib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol

import httpx

from app.settings.config import Settings

logger = logging.getLogger(__name__)

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


class DeliveryProvider(str, enum.Enum):
    BREVO = "brevo"
    SMTP = "smtp"


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


# Brevo statuses that no amount of retrying fixes: malformed request, bad/forbidden
# API key, unknown resource, unprocessable content. Everything else (408, 429, 5xx)
# is worth retrying.
_BREVO_PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 422})


class EmailProvider(Protocol):
    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        from_email: str,
        from_name: str | None = None,
    ) -> ProviderResult: ...


class BrevoEmailProvider:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        self._api_key = api_key
        self._client = client

    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        from_email: str,
        from_name: str | None = None,
    ) -> ProviderResult:
        payload = {
            "sender": {"email": from_email, "name": from_name or from_email},
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html,
        }
        headers = {"api-key": self._api_key, "content-type": "application/json"}

        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=10.0)
        try:
            response = await client.post(BREVO_SEND_URL, json=payload, headers=headers)
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
                f"Brevo send failed ({response.status_code}): {response.text}",
                permanent=response.status_code in _BREVO_PERMANENT_STATUSES,
            )

        data = response.json()
        return ProviderResult(provider_message_id=data.get("messageId", ""))


def _smtp_error_is_permanent(exc: Exception) -> bool:
    """SMTP 5xx replies and refused senders/recipients are final; 4xx replies and
    transport errors (connection refused, timeouts) are transient."""
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return True
    if isinstance(exc, smtplib.SMTPResponseException):
        return exc.smtp_code >= 500
    return False


class SmtpEmailProvider:
    """Plain SMTP delivery (no TLS/auth): the mailpit sandbox in local dev, or a
    project-specific relay host chosen in the admin.
    """

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port

    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        from_email: str,
        from_name: str | None = None,
    ) -> ProviderResult:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{from_name} <{from_email}>" if from_name else from_email
        message["To"] = to
        message.set_content(html, subtype="html")

        try:
            await asyncio.to_thread(self._send_sync, message)
        except Exception as exc:
            logger.warning(
                "smtp send failed (%s:%s): %s",
                self._host,
                self._port,
                type(exc).__name__,
                extra={"event": "email_provider_error", "provider": "smtp"},
            )
            raise EmailSendError(
                f"SMTP send failed ({self._host}:{self._port}): {exc}",
                permanent=_smtp_error_is_permanent(exc),
            ) from exc
        return ProviderResult(provider_message_id=str(uuid.uuid4()))

    def _send_sync(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=10) as client:
            client.send_message(message)


def get_email_provider_for_project(
    provider: str, config: Mapping[str, Any] | None, settings: Settings
) -> EmailProvider:
    """Resolves a project's stored delivery settings to a provider instance.

    Takes plain values (not the Project ORM object) so core/ never imports a
    domain package. An unknown provider name fails loudly instead of silently
    falling back to another channel.
    """
    config = config or {}
    if provider == DeliveryProvider.BREVO.value:
        return BrevoEmailProvider(api_key=settings.brevo_api_key)
    if provider == DeliveryProvider.SMTP.value:
        return SmtpEmailProvider(
            host=str(config.get("smtp_host") or settings.smtp_host),
            port=int(config.get("smtp_port") or settings.smtp_port),
        )
    raise EmailSendError(f"Unknown delivery provider {provider!r}", permanent=True)
