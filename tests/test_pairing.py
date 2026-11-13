"""Pairing link: staff approves a WordPress site, the plugin trades a one-time code for a key."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.pairing.models import PairingCode
from tests.conftest import create_project_and_api_key

RETURN_URL = "https://ediyporn.com/wp-admin/admin-post.php?action=ediy_relay_connect_callback"
QUERY = {"return_url": RETURN_URL, "state": "s-123", "site": "ediyporn.com"}


async def _approve(admin_client: AsyncClient, project_id: str, **extra: str) -> tuple[str, str]:
    page = await admin_client.get("/admin/connect", params=QUERY)
    assert page.status_code == 200, page.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    form = {
        "return_url": RETURN_URL,
        "state": "s-123",
        "project_id": project_id,
        "csrf": csrf,
        "label": "wordpress ediyporn.com",
        **extra,
    }
    done = await admin_client.post("/admin/connect", data=form)
    assert done.status_code == 200, done.text
    link = re.findall(r'<a class="link" href="([^"]+)"', done.text)[0].replace("&amp;", "&")
    params = parse_qs(urlparse(link).query)
    return params["code"][0], params["state"][0]


async def test_consent_needs_a_session_and_login_resumes_it(
    client: AsyncClient, db_session
) -> None:
    response = await client.get("/admin/connect", params=QUERY, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login?next=%2Fadmin%2Fconnect%3F")

    from app.auth.repository import AdminUserRepository
    from app.auth.service import AuthService

    await AuthService(AdminUserRepository(db_session)).register("a@devbutter.com", "supersecret")
    back = await client.post(
        "/admin/login",
        data={"email": "a@devbutter.com", "password": "supersecret", "next": "/admin/connect?x=1"},
        follow_redirects=False,
    )
    assert back.headers["location"] == "/admin/connect?x=1"
    evil = await client.post(
        "/admin/login",
        data={"email": "a@devbutter.com", "password": "supersecret", "next": "https://evil.test/"},
        follow_redirects=False,
    )
    assert evil.headers["location"] == "/admin"


async def test_consent_rejects_return_urls_that_are_not_the_plugin_callback(admin_client) -> None:
    for bad in (
        "https://evil.test/steal",
        "http://ediyporn.com/wp-admin/admin-post.php?action=ediy_relay_connect_callback",
        "https://ediyporn.com/wp-admin/admin-post.php?action=other",
        "javascript:alert(1)",
    ):
        response = await admin_client.get("/admin/connect", params={**QUERY, "return_url": bad})
        assert response.status_code == 422, (bad, response.headers.get("location"))


async def test_approve_then_exchange_gives_a_send_only_key_once(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    project_id, _ = await create_project_and_api_key(db_session)
    code, state = await _approve(admin_client, project_id)
    assert code.startswith("brp_") and state == "s-123"

    first = await admin_client.post("/api/v1/connect/exchange", json={"code": code})
    body = first.json()
    assert first.status_code == 200
    assert body["plaintext_key"].startswith("brk_") and body["scopes"] == ["send"]

    replay = await admin_client.post("/api/v1/connect/exchange", json={"code": code})
    assert replay.status_code == 401

    auth = {"Authorization": f"Bearer {body['plaintext_key']}"}
    assert (await admin_client.get("/api/v1/messages/", headers=auth)).status_code == 403


async def test_code_is_stored_hashed_and_expires(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    project_id, _ = await create_project_and_api_key(db_session)
    code, _ = await _approve(admin_client, project_id)
    row = (await db_session.execute(PairingCode.__table__.select())).first()
    assert code not in str(tuple(row)) and len(row.code_hash) == 64

    await db_session.execute(
        update(PairingCode).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db_session.commit()
    assert (
        await admin_client.post("/api/v1/connect/exchange", json={"code": code})
    ).status_code == 401


async def test_exchange_rejects_junk_and_unknown_codes(client: AsyncClient) -> None:
    assert (
        await client.post("/api/v1/connect/exchange", json={"code": "nope-nope-nope"})
    ).status_code == 422
    assert (
        await client.post("/api/v1/connect/exchange", json={"code": "brp_" + "x" * 40})
    ).status_code == 401


async def test_approve_needs_the_csrf_token_and_an_active_project(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    project_id, _ = await create_project_and_api_key(db_session)
    await admin_client.get("/admin/connect", params=QUERY)
    bad = await admin_client.post(
        "/admin/connect",
        data={"return_url": RETURN_URL, "state": "s", "project_id": project_id, "csrf": "wrong"},
    )
    assert bad.status_code == 422

    page = await admin_client.get("/admin/connect", params=QUERY)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    missing = await admin_client.post(
        "/admin/connect",
        data={
            "return_url": RETURN_URL,
            "state": "s",
            "project_id": str(uuid.uuid4()),
            "csrf": csrf,
        },
    )
    assert missing.status_code == 422


async def test_public_connect_link_goes_to_the_admin_host(client: AsyncClient, monkeypatch) -> None:
    from app.settings.config import settings

    monkeypatch.setattr(settings, "admin_base_url", "https://relay-admin.devbutter.tech")
    response = await client.get("/connect?state=s", follow_redirects=False)
    assert (
        response.headers["location"] == "https://relay-admin.devbutter.tech/admin/connect?state=s"
    )
