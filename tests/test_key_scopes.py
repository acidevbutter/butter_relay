"""DVB-308 / S3: scopes, configurable expiry, rotation and per-key rate limit."""

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_keys.repository import ApiKeyRepository
from app.api_keys.service import ApiKeyService
from app.projects.repository import ProjectRepository
from app.projects.schemas import ProjectUpdate
from app.projects.service import ProjectService
from tests.conftest import create_project_and_api_key


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _service(db: AsyncSession) -> ApiKeyService:
    return ApiKeyService(ApiKeyRepository(db), ProjectRepository(db))


async def test_scopes_gate_every_route(client: AsyncClient, db_session: AsyncSession) -> None:
    project_id, _ = await create_project_and_api_key(db_session)
    pid = uuid.UUID(project_id)
    service = _service(db_session)
    _, send_only = await service.create(pid, "send", scopes=["send"])
    _, read_only = await service.create(pid, "read", scopes=["read"])
    _, manager = await service.create(pid, "manage", scopes=["manage"])
    template = {
        "project_id": project_id, "key": "k", "subject_template": "s", "html_template": "<p/>",
    }

    assert (await client.get("/api/v1/messages/", headers=_auth(send_only))).status_code == 403
    assert (await client.get("/api/v1/messages/", headers=_auth(read_only))).status_code == 200
    assert (await client.get("/api/v1/email-types", headers=_auth(send_only))).status_code == 403
    denied = await client.post("/api/v1/templates/", json=template, headers=_auth(read_only))
    assert denied.status_code == 403
    assert "manage" in denied.json()["detail"]
    allowed = await client.post("/api/v1/templates/", json=template, headers=_auth(manager))
    assert allowed.status_code == 201
    keys = await client.get(
        f"/api/v1/api-keys/?project_id={project_id}", headers=_auth(read_only)
    )
    assert keys.status_code == 403


async def test_plaintext_only_on_creation(client: AsyncClient, db_session: AsyncSession) -> None:
    project_id, key = await create_project_and_api_key(db_session)
    created = await client.post(
        "/api/v1/api-keys/", headers=_auth(key),
        json={"project_id": project_id, "label": "wp", "scopes": ["send", "read"],
              "expires_in_days": 90},
    )
    listing = await client.get(f"/api/v1/api-keys/?project_id={project_id}", headers=_auth(key))

    body = created.json()
    assert created.status_code == 201 and body["plaintext_key"].startswith("brk_")
    assert body["scopes"] == ["send", "read"] and body["expires_at"] is not None
    assert body["plaintext_key"] not in listing.text and "key_hash" not in listing.text


async def test_rotation_keeps_old_key_until_revoked_or_grace(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    project_id, admin_key = await create_project_and_api_key(db_session)
    pid = uuid.UUID(project_id)
    service = _service(db_session)
    old, old_plain = await service.create(pid, "wordpress-prod", scopes=["send", "read"])

    rotated = await client.post(
        f"/api/v1/api-keys/{old.id}/rotate?project_id={project_id}",
        headers=_auth(admin_key), json={},
    )
    new_plain = rotated.json()["plaintext_key"]
    assert rotated.status_code == 201
    assert rotated.json()["label"] == "wordpress-prod"
    assert rotated.json()["scopes"] == ["send", "read"]
    # Both work during the rotation.
    for plain in (old_plain, new_plain):
        assert (await client.get("/api/v1/messages/", headers=_auth(plain))).status_code == 200

    # With a 0h grace the old key expires right away.
    newer, _ = await service.rotate(old.id, pid, grace_hours=0)
    assert (await client.get("/api/v1/messages/", headers=_auth(old_plain))).status_code == 401
    assert newer.label == "wordpress-prod"


async def test_rate_limit_per_key_with_project_override(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    project_id, key = await create_project_and_api_key(db_session)
    pid = uuid.UUID(project_id)
    await ProjectService(ProjectRepository(db_session)).update(
        pid, ProjectUpdate(rate_per_minute=2)
    )
    _, other_key = await _service(db_session).create(pid, "other")

    statuses = [
        (await client.get("/api/v1/messages/", headers=_auth(key))).status_code for _ in range(3)
    ]
    limited = await client.get("/api/v1/messages/", headers=_auth(key))

    assert statuses == [200, 200, 429]
    assert limited.json()["code"] == "rate_limited"
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    # Counters are per key.
    assert (await client.get("/api/v1/messages/", headers=_auth(other_key))).status_code == 200


async def test_admin_creates_scoped_expiring_key_and_rotates(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    from tests.test_admin import _create_project, _project_id

    await _create_project(admin_client)
    project_id = await _project_id(db_session)
    page = await admin_client.post(
        f"/admin/projects/{project_id}/api-keys",
        data={"label": "wp", "scopes": ["send"], "expires_in_days": "7"},
    )
    key = (await _service(db_session).list_for_project(project_id))[0]
    rotated = await admin_client.post(
        f"/admin/projects/{project_id}/api-keys/{key.id}/rotate", data={"grace_hours": "24"}
    )

    assert "New key (shown once" in page.text
    assert key.scopes == ["send"] and key.expires_at is not None
    assert "Key rotated" in rotated.text and "(expires in 24h)" in rotated.text
    assert len(await _service(db_session).list_for_project(project_id)) == 2
