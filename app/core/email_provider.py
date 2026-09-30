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
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol

import httpx

from app.core.metrics import emit_metrics
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
    def __init__(self, detail: str):
        self.detail = detail


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
        start = time.perf_counter()
        try:
            response = await client.post(BREVO_SEND_URL, json=payload, headers=headers)
        except Exception:
            await emit_metrics([("EmailProviderErrorCount", 1, "Count")], {"Provider": "brevo"})
            raise
        finally:
            if owns_client:
                await client.aclose()

        await emit_metrics(
            [("EmailProviderLatency", (time.perf_counter() - start) * 1000, "Milliseconds")],
            {"Provider": "brevo"},
        )
        if response.status_code >= 400:
            await emit_metrics([("EmailProviderErrorCount", 1, "Count")], {"Provider": "brevo"})
            raise EmailSendError(f"Brevo send failed ({response.status_code}): {response.text}")

        data = response.json()
        return ProviderResult(provider_message_id=data.get("messageId", ""))


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

        start = time.perf_counter()
        try:
            await asyncio.to_thread(self._send_sync, message)
        except Exception as exc:
            await emit_metrics([("EmailProviderErrorCount", 1, "Count")], {"Provider": "smtp"})
            raise EmailSendError(f"SMTP send failed ({self._host}:{self._port}): {exc}") from exc
        await emit_metrics(
            [("EmailProviderLatency", (time.perf_counter() - start) * 1000, "Milliseconds")],
            {"Provider": "smtp"},
        )
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
    raise EmailSendError(f"Unknown delivery provider {provider!r}")
