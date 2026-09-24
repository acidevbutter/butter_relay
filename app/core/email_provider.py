"""Email delivery abstraction.

EmailProvider is a Protocol so the worker/service layer never depends on a
specific vendor SDK. BrevoEmailProvider is the only implementation built
today. The planned next implementation is `SesEmailProvider` (IAM-authenticated,
no separate API key/account needed since deployment is already on AWS) -- not
built in this pass, just designed for: swapping providers only means adding a
new class here and changing the dependency wired in app/messages/router.py or
app/worker/consumer.py, nothing in the service layer.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import time
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import httpx

from app.core.metrics import emit_metrics
from app.settings.config import Settings

logger = logging.getLogger(__name__)

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


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
    """Plain SMTP delivery for local dev -- talks to mailpit (no TLS/auth),
    never used outside environment == "development" (see get_email_provider).
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
        except Exception:
            await emit_metrics([("EmailProviderErrorCount", 1, "Count")], {"Provider": "smtp"})
            raise
        await emit_metrics(
            [("EmailProviderLatency", (time.perf_counter() - start) * 1000, "Milliseconds")],
            {"Provider": "smtp"},
        )
        return ProviderResult(provider_message_id=str(uuid.uuid4()))

    def _send_sync(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=10) as client:
            client.send_message(message)


def get_email_provider(settings: Settings) -> EmailProvider:
    if settings.environment == "development":
        return SmtpEmailProvider(host=settings.smtp_host, port=settings.smtp_port)
    return BrevoEmailProvider(api_key=settings.brevo_api_key)
