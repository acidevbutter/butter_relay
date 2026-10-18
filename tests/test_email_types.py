"""DVB-307 / S2: e-mail type catalogue and template upload."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationFailedError
from app.email_types.repository import EmailTypeRepository
from app.email_types.schemas import EmailTypeCreate, EmailTypeMode
from app.email_types.service import EmailTypeService
from app.templates_.repository import TemplateRepository
from tests.conftest import create_project_and_api_key


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def test_upsert_is_idempotent(db_session: AsyncSession) -> None:
    project_id, _ = await create_project_and_api_key(db_session)
    service = EmailTypeService(EmailTypeRepository(db_session))
    pid = uuid.UUID(project_id)
    payload = EmailTypeCreate(key="welcome", mode=EmailTypeMode.TEMPLATE)

    assert (await service.upsert(pid, payload))[1] == "created"
    assert (await service.upsert(pid, payload))[1] == "unchanged"
    changed = payload.model_copy(update={"description": "Boas-vindas"})
    assert (await service.upsert(pid, changed))[1] == "updated"
    assert len(await service.list(pid)) == 1


@pytest.mark.parametrize("key", ["A", "x", "has space", "-lead", "a" * 101])
def test_email_type_key_format(key: str) -> None:
    with pytest.raises(ValueError):
        EmailTypeCreate(key=key)


async def test_resolve_for_send_codes(db_session: AsyncSession) -> None:
    project_id, _ = await create_project_and_api_key(db_session)
    pid = uuid.UUID(project_id)
    service = EmailTypeService(EmailTypeRepository(db_session))
    await service.upsert(pid, EmailTypeCreate(key="order.raw", mode=EmailTypeMode.RAW))

    assert (await service.resolve_for_send(
        pid, "order.raw", EmailTypeMode.RAW, allow_undeclared=False
    )) is not None
    with pytest.raises(ValidationFailedError) as unknown:
        await service.resolve_for_send(pid, "nope", EmailTypeMode.RAW, allow_undeclared=False)
    assert unknown.value.code == "unknown_email_type"
    assert await service.resolve_for_send(
        pid, "nope", EmailTypeMode.RAW, allow_undeclared=True
    ) is None
    with pytest.raises(ValidationFailedError) as mismatch:
        await service.resolve_for_send(
            pid, "order.raw", EmailTypeMode.TEMPLATE, allow_undeclared=False
        )
    assert mismatch.value.code == "email_type_mode_mismatch"


async def test_discovery_lists_only_own_active_types(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    mine, key = await create_project_and_api_key(db_session, label="mine")
    other, _ = await create_project_and_api_key(db_session, label="other")
    service = EmailTypeService(EmailTypeRepository(db_session))
    await service.upsert(uuid.UUID(mine), EmailTypeCreate(key="welcome"))
    off, _ = await service.upsert(uuid.UUID(mine), EmailTypeCreate(key="old.one"))
    await service.set_active(uuid.UUID(mine), off.id, False)
    await service.upsert(uuid.UUID(other), EmailTypeCreate(key="theirs"))

    assert (await client.get("/api/v1/email-types")).status_code == 401
    body = (await client.get("/api/v1/email-types", headers=_auth(key))).json()
    assert [t["key"] for t in body] == ["welcome"]


async def _upload(client: AsyncClient, key: str, html: bytes, **form: str) -> object:
    return await client.post(
        "/api/v1/templates/upload",
        headers=_auth(key),
        files={"file": ("welcome.html", html, "text/html")},
        data={"key": "welcome", "subject_template": "Oi {{ name }}", **form},
    )


async def test_upload_creates_once_and_dedupes_by_content(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    project_id, key = await create_project_and_api_key(db_session)

    first = await _upload(client, key, b"<p>Oi {{ name }}</p>")
    again = await _upload(client, key, b"<p>Oi {{ name }}</p>")
    changed = await _upload(client, key, b"<p>Ola {{ name }}</p>")

    assert first.status_code == 201 and first.json()["version"] == 1  # type: ignore[attr-defined]
    assert again.status_code == 200 and again.json()["version"] == 1  # type: ignore[attr-defined]
    assert changed.status_code == 201 and changed.json()["version"] == 2  # type: ignore[attr-defined]
    templates = await TemplateRepository(db_session).list_for_project(uuid.UUID(project_id))
    assert [(t.version, t.is_active) for t in sorted(templates, key=lambda t: t.version)] == [
        (1, False), (2, True),
    ]


async def test_broken_upload_is_rejected_without_touching_the_active_version(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    project_id, key = await create_project_and_api_key(db_session)
    await _upload(client, key, b"<p>Oi {{ name }}</p>")

    broken = await _upload(client, key, b"<p>Oi {{ name </p>")
    too_big = await _upload(client, key, b"x" * (512 * 1024 + 1))
    not_html = await client.post(
        "/api/v1/templates/upload", headers=_auth(key),
        files={"file": ("a.txt", b"hi", "text/plain")},
        data={"key": "welcome", "subject_template": "s"},
    )

    assert broken.status_code == 422  # type: ignore[attr-defined]
    assert too_big.status_code == 422 and too_big.json()["code"] == "file_too_large"  # type: ignore[attr-defined]
    assert not_html.status_code == 422 and not_html.json()["code"] == "invalid_file"
    active = await TemplateRepository(db_session).get_active(uuid.UUID(project_id), "welcome")
    assert active is not None and active.version == 1


async def test_upload_without_activation_keeps_current_version(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    project_id, key = await create_project_and_api_key(db_session)
    await _upload(client, key, b"<p>v1 {{ name }}</p>")
    draft = await _upload(client, key, b"<p>v2 {{ name }}</p>", activate="false")

    assert draft.json()["is_active"] is False  # type: ignore[attr-defined]
    active = await TemplateRepository(db_session).get_active(uuid.UUID(project_id), "welcome")
    assert active is not None and active.version == 1


async def test_admin_email_types_and_file_upload(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    from tests.test_admin import _create_project, _project_id

    await _create_project(admin_client)
    project_id = await _project_id(db_session)

    saved = await admin_client.post(
        f"/admin/projects/{project_id}/email-types",
        data={"key": "welcome", "mode": "template", "category": "transactional",
              "sample_payload": '{"name": "Ana"}'},
    )
    published = await admin_client.post(
        f"/admin/projects/{project_id}/templates",
        data={"key": "welcome", "subject_template": "Oi {{ name }}"},
        files={"html_file": ("welcome.html", b"<p>Oi {{ name }}</p>", "text/html")},
    )
    email_type = (await EmailTypeService(EmailTypeRepository(db_session)).list(project_id))[0]
    preview = await admin_client.post(
        f"/admin/projects/{project_id}/email-types/{email_type.id}/preview"
    )
    toggled = await admin_client.post(
        f"/admin/projects/{project_id}/email-types/{email_type.id}/toggle"
    )

    assert "E-mail type &#39;welcome&#39; created." in saved.text
    assert "Published &#39;welcome&#39; v1" in published.text
    assert "Oi Ana" in preview.text
    assert "deactivated" in toggled.text
