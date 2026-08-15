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

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

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
    ) -> ProviderResult:
        ...


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
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise EmailSendError(f"Brevo send failed ({response.status_code}): {response.text}")

        data = response.json()
        return ProviderResult(provider_message_id=data.get("messageId", ""))
