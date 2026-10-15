"""Covers app/api_keys/service.py and the /api/v1/api-keys/* auth added by
docs/prompts/autenticar-api-v1.md (see docs/auditoria-seguranca-2026-08-29.md item 1/2).
Broader coverage of this module is tracked in docs/prompts/cobertura-testes-messages-api-keys.md.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.api_keys.repository import ApiKeyRepository
from app.api_keys.service import ApiKeyService
from app.core.exceptions import NotFoundError, UnauthorizedError
from app.projects.repository import ProjectRepository
from tests.conftest import create_project_and_api_key


def _auth(plaintext_key: str) -> dict:
    return {"Authorization": f"Bearer {plaintext_key}"}


def _service(db_session) -> ApiKeyService:
    return ApiKeyService(ApiKeyRepository(db_session), ProjectRepository(db_session))


# --- ApiKeyService, called directly (documents that `authenticate` -- previously
# dead code, see docs/auditoria-seguranca-2026-08-29.md item 2 -- actually works) ---

async def test_authenticate_with_valid_key_returns_api_key_and_touches_last_used(db_session):
    service = _service(db_session)
    project_id, plaintext_key = await create_project_and_api_key(db_session)

    api_key = await service.authenticate(plaintext_key)

    assert str(api_key.project_id) == project_id
    assert api_key.last_used_at is not None


async def test_authenticate_with_unknown_key_raises_unauthorized(db_session):
    service = _service(db_session)
    raised = False
    try:
        await service.authenticate("brk_does-not-exist")
    except UnauthorizedError:
        raised = True
    assert raised


async def test_authenticate_with_revoked_key_raises_unauthorized(db_session):
    service = _service(db_session)
    project_id, plaintext_key = await create_project_and_api_key(db_session)
    api_key = await service.authenticate(plaintext_key)
    await service.revoke(api_key.id, api_key.project_id)

    raised = False
    try:
        await service.authenticate(plaintext_key)
    except UnauthorizedError:
        raised = True
    assert raised


async def test_internal_project_key_never_expires(db_session):
    service = _service(db_session)
    _, plaintext_key = await create_project_and_api_key(db_session, external=False)

    api_key = await service.authenticate(plaintext_key)

    assert api_key.expires_at is None


async def test_external_project_key_expires_in_30_days(db_session):
    service = _service(db_session)
    _, plaintext_key = await create_project_and_api_key(db_session, external=True)

    api_key = await service.authenticate(plaintext_key)

    assert api_key.expires_at is not None
    # SQLite (used in tests) doesn't round-trip tzinfo, unlike Postgres.
    expires_at = api_key.expires_at.replace(tzinfo=UTC)
    assert expires_at > datetime.now(UTC) + timedelta(days=29)
    assert expires_at < datetime.now(UTC) + timedelta(days=31)


async def test_authenticate_with_expired_key_raises_unauthorized_with_specific_message(db_session):
    service = _service(db_session)
    _, plaintext_key = await create_project_and_api_key(db_session, external=True)
    api_key = await service.authenticate(plaintext_key)
    api_key.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    try:
        await service.authenticate(plaintext_key)
        raised_detail = None
    except UnauthorizedError as exc:
        raised_detail = exc.detail
    assert raised_detail is not None
    assert "expired" in raised_detail


async def test_revoke_unknown_key_raises_not_found(db_session):
    service = _service(db_session)
    project_id, _ = await create_project_and_api_key(db_session)

    raised = False
    try:
        await service.revoke(
            uuid.UUID("00000000-0000-0000-0000-000000000000"), uuid.UUID(project_id)
        )
    except NotFoundError:
        raised = True
    assert raised


# --- HTTP: /api/v1/api-keys/* ---

async def test_create_api_key_without_authorization_header_returns_401(client, db_session):
    project_id, _ = await create_project_and_api_key(db_session)
    response = await client.post(
        "/api/v1/api-keys/", json={"project_id": project_id, "label": "new-key"}
    )
    assert response.status_code == 401


async def test_create_api_key_for_other_project_returns_403(client, db_session):
    project_id, _ = await create_project_and_api_key(db_session, label="owner")
    _, other_key = await create_project_and_api_key(db_session, label="intruder")

    response = await client.post(
        "/api/v1/api-keys/",
        json={"project_id": project_id, "label": "new-key"},
        headers=_auth(other_key),
    )
    assert response.status_code == 403


async def test_create_api_key_for_own_project_returns_201(client, db_session):
    project_id, key = await create_project_and_api_key(db_session)

    response = await client.post(
        "/api/v1/api-keys/",
        json={"project_id": project_id, "label": "rotated-key"},
        headers=_auth(key),
    )
    assert response.status_code == 201
    assert response.json()["plaintext_key"].startswith("brk_")


async def test_list_api_keys_for_other_project_returns_403(client, db_session):
    project_id, _ = await create_project_and_api_key(db_session, label="owner")
    _, other_key = await create_project_and_api_key(db_session, label="intruder")

    response = await client.get(
        "/api/v1/api-keys/", params={"project_id": project_id}, headers=_auth(other_key)
    )
    assert response.status_code == 403
