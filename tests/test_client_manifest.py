"""DVB-310 / S5: service token, client + key admin API and the idempotent manifest."""

import hashlib
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings.config import settings
from tests.conftest import create_project_and_api_key

TOKEN = "service-token-for-tests"
SERVICE = {"Authorization": f"Bearer {TOKEN}"}
MANIFEST: dict[str, Any] = {
    "name": "EDIY",
    "default_sender_email": "noreply@ediy.example",
    "default_sender_name": "EDIY",
    "secrets": {"brevo_api_key": "xkeysib-secret"},
    "email_types": [
        {"key": "welcome", "category": "transactional", "mode": "template"},
        {"key": "newsletter", "category": "marketing", "mode": "template"},
    ],
    "templates": [
        {
            "key": "welcome",
            "subject_template": "Olá {{ name }}",
            "html_template": "<p>Bem-vindo, {{ name }}</p>",
        }
    ],
}


@pytest.fixture(autouse=True)
def _service_token(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        settings, "relay_admin_token_hash", hashlib.sha256(TOKEN.encode()).hexdigest()
    )
    yield


async def _apply(
    client: AsyncClient, manifest: dict[str, Any] = MANIFEST, **params: Any
) -> Response:
    return await client.put(
        "/api/v1/admin/clients/ediy/manifest", json=manifest, params=params, headers=SERVICE
    )


async def test_admin_routes_need_session_or_service_token(client: AsyncClient) -> None:
    url = "/api/v1/admin/manifest/schema"
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers={"Authorization": "Bearer nope"})).status_code == 401
    assert (await client.get(url, headers=SERVICE)).status_code == 200


async def test_service_token_is_disabled_without_hash(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "relay_admin_token_hash", "")
    response = await client.get("/api/v1/admin/manifest/schema", headers=SERVICE)
    assert response.status_code == 401


async def test_service_token_cannot_reach_messages_or_templates(client: AsyncClient) -> None:
    for path in ("/api/v1/messages/", "/api/v1/templates/", "/api/v1/email-types"):
        assert (await client.get(path, headers=SERVICE)).status_code == 401


async def test_project_api_key_cannot_use_admin_routes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, key = await create_project_and_api_key(db_session)
    response = await client.get(
        "/api/v1/admin/manifest/schema", headers={"Authorization": f"Bearer {key}"}
    )
    assert response.status_code == 401


async def test_create_client_and_manage_keys(client: AsyncClient) -> None:
    body = {
        "name": "EDIY", "slug": "ediy", "external": True,
        "default_sender_email": "noreply@ediy.example", "default_sender_name": "EDIY",
    }
    created = await client.post("/api/v1/admin/clients", headers=SERVICE, json=body)
    assert created.status_code == 201
    duplicate = await client.post("/api/v1/admin/clients", headers=SERVICE, json=body)
    assert duplicate.status_code == 409

    key = await client.post(
        "/api/v1/admin/clients/ediy/keys", headers=SERVICE, json={"label": "wp"}
    )
    assert key.status_code == 201 and key.json()["plaintext_key"].startswith("brk_")
    listing = await client.get("/api/v1/admin/clients/ediy/keys", headers=SERVICE)
    assert "plaintext_key" not in listing.json()[0]

    key_id = key.json()["id"]
    rotated = await client.post(
        f"/api/v1/admin/clients/ediy/keys/{key_id}/rotate", headers=SERVICE, json={}
    )
    assert rotated.status_code == 201
    revoked = await client.post(
        f"/api/v1/admin/clients/ediy/keys/{key_id}/revoke", headers=SERVICE
    )
    assert revoked.json()["revoked_at"] is not None


async def test_apply_twice_changes_nothing(client: AsyncClient) -> None:
    first = await _apply(client)
    assert first.status_code == 200
    body = first.json()
    assert body["project"] == "created"
    assert sorted(body["email_types"]["created"]) == ["newsletter", "welcome"]
    assert body["templates"]["created"] == ["welcome"]
    assert body["secrets_set"] == ["brevo_api_key"]

    second = (await _apply(client)).json()
    assert second["project"] == "unchanged" and second["secrets_set"] == []
    for section in ("email_types", "templates"):
        assert second[section]["created"] == second[section]["updated"] == []
        assert second[section]["pruned"] == []


async def test_dry_run_writes_nothing(client: AsyncClient) -> None:
    preview = await _apply(client, dry_run=True)
    assert preview.status_code == 200 and preview.json()["dry_run"] is True
    assert preview.json()["project"] == "created"
    missing = await client.get("/api/v1/admin/clients/ediy", headers=SERVICE)
    assert missing.status_code == 404


async def test_changes_update_and_prune_deactivates(client: AsyncClient) -> None:
    await _apply(client)
    changed = {
        **MANIFEST,
        "email_types": [MANIFEST["email_types"][0]],
        "templates": [{**MANIFEST["templates"][0], "subject_template": "Oi {{ name }}"}],
    }
    kept = (await _apply(client, changed)).json()
    assert kept["templates"]["updated"] == ["welcome"] and kept["email_types"]["pruned"] == []

    pruned = (await _apply(client, changed, prune=True)).json()
    assert pruned["email_types"]["pruned"] == ["newsletter"]
    exported = (await client.get("/api/v1/admin/clients/ediy/manifest", headers=SERVICE)).json()
    assert exported["templates"][0]["subject_template"] == "Oi {{ name }}"
    again = (await _apply(client, changed, prune=True)).json()
    assert again["email_types"]["pruned"] == [] and again["templates"]["updated"] == []


async def test_export_never_contains_secrets_and_round_trips(client: AsyncClient) -> None:
    await _apply(client)
    response = await client.get("/api/v1/admin/clients/ediy/manifest", headers=SERVICE)
    assert response.status_code == 200
    assert "xkeysib-secret" not in response.text
    exported = response.json()
    assert exported["secrets"] is None
    replay = (await _apply(client, exported)).json()
    assert replay["project"] == "unchanged"
    assert replay["email_types"]["created"] == replay["templates"]["created"] == []


async def test_invalid_manifest_is_422_with_paths_and_stores_nothing(client: AsyncClient) -> None:
    bad = {
        **MANIFEST,
        "email_types": [MANIFEST["email_types"][0], MANIFEST["email_types"][0]],
        "templates": [{**MANIFEST["templates"][0], "html_template": "<p>{{ unclosed </p>"}],
    }
    response = await _apply(client, bad)
    assert response.status_code == 422
    paths = [e["loc"] for e in response.json()["errors"]]
    assert ["email_types", 1, "key"] in paths and ["templates", 0] in paths
    assert (await client.get("/api/v1/admin/clients/ediy", headers=SERVICE)).status_code == 404

    unknown = await _apply(client, {**MANIFEST, "bogus": 1})
    assert unknown.status_code == 422


async def test_manifest_schema_lists_fields(client: AsyncClient) -> None:
    schema = (await client.get("/api/v1/admin/manifest/schema", headers=SERVICE)).json()
    assert {"name", "email_types", "templates", "secrets"} <= set(schema["properties"])
