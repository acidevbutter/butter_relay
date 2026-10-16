import asyncio

import httpx
import pytest

from app.core.blob_client import BlobClient
from app.settings.config import settings


def test_blob_client_survives_one_event_loop_per_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each Celery task runs its own asyncio.run(); the same BlobClient instance
    must keep working across those loops (acidevbutter/butter_relay#63)."""
    monkeypatch.setattr(settings, "butter_blob_base_url", "https://blob.test")
    monkeypatch.setattr(settings, "butter_blob_api_key", "bbk_test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1]})

    client = BlobClient(transport=httpx.MockTransport(handler))

    assert asyncio.run(client.get("first"))["id"] == "first"
    assert asyncio.run(client.get("second"))["id"] == "second"
