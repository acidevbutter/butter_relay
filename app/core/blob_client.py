from __future__ import annotations

from typing import Any, cast

import httpx

from app.core.decorators import log_call
from app.core.exceptions import ValidationFailedError
from app.settings.config import settings


class BlobClient:
    """Reads NFS-e blobs in the relay namespace. Never logs document bytes.

    Each call opens its own httpx.AsyncClient: every Celery task runs in a fresh
    asyncio.run() loop, and a client kept across tasks is bound to the first loop,
    so the next call in the same worker process died with "Event loop is closed"
    (acidevbutter/butter_relay#63, same class of bug as #29).
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"X-Blob-Namespace": settings.butter_blob_namespace}
        key = settings.butter_blob_api_key
        if key.startswith("bbk_"):
            headers["Authorization"] = f"Bearer {key}"
        elif key:
            headers["X-Service-Api-Key"] = key
        return headers

    @log_call
    async def get(self, blob_id: str) -> dict[str, Any]:
        if not settings.butter_blob_base_url or not settings.butter_blob_api_key:
            raise ValidationFailedError("Butter Blob is not configured")
        try:
            async with httpx.AsyncClient(
                base_url=settings.butter_blob_base_url,
                timeout=settings.butter_blob_timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/internal/v1/blobs/{blob_id}",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise ValidationFailedError(f"butter_blob request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ValidationFailedError(
                f"butter_blob returned {response.status_code}",
            )
        return cast(dict[str, Any], response.json())


_client: BlobClient | None = None


def get_blob_client() -> BlobClient:
    global _client
    if _client is None:
        _client = BlobClient()
    return _client
