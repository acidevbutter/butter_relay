import uuid

from app.templates_.repository import TemplateRepository
from app.templates_.schemas import TemplateCreate
from app.templates_.service import TemplateService
from tests.conftest import create_project_and_api_key


def _auth(plaintext_key: str) -> dict:
    return {"Authorization": f"Bearer {plaintext_key}"}


async def test_publish_and_preview_template(client, db_session):
    project_id, key = await create_project_and_api_key(db_session)

    publish = await client.post(
        "/api/v1/templates/",
        json={
            "project_id": project_id,
            "key": "welcome_email",
            "subject_template": "Welcome, {{ name }}!",
            "html_template": "<p>Hi {{ name }}</p>",
        },
        headers=_auth(key),
    )
    assert publish.status_code == 201
    template_id = publish.json()["id"]
    assert publish.json()["version"] == 1

    preview = await client.post(
        f"/api/v1/templates/{template_id}/preview",
        json={"payload": {"name": "Marcos"}},
        headers=_auth(key),
    )
    assert preview.status_code == 200
    assert preview.json() == {"subject": "Welcome, Marcos!", "html": "<p>Hi Marcos</p>"}


async def test_publishing_new_version_deactivates_prior(client, db_session):
    project_id, key = await create_project_and_api_key(db_session)
    body = {
        "project_id": project_id,
        "key": "welcome_email",
        "subject_template": "v1 subject",
        "html_template": "<p>v1</p>",
    }
    v1 = await client.post("/api/v1/templates/", json=body, headers=_auth(key))
    assert v1.json()["version"] == 1
    assert v1.json()["is_active"] is True

    body["subject_template"] = "v2 subject"
    v2 = await client.post("/api/v1/templates/", json=body, headers=_auth(key))
    assert v2.json()["version"] == 2
    assert v2.json()["is_active"] is True

    listing = await client.get(
        "/api/v1/templates/", params={"project_id": project_id}, headers=_auth(key)
    )
    versions = {t["version"]: t["is_active"] for t in listing.json()}
    assert versions == {1: False, 2: True}


async def test_template_render_rejects_payload_violating_schema(client, db_session):
    project_id, key = await create_project_and_api_key(db_session)
    publish = await client.post(
        "/api/v1/templates/",
        json={
            "project_id": project_id,
            "key": "welcome_email",
            "subject_template": "Welcome, {{ name }}!",
            "html_template": "<p>Hi {{ name }}</p>",
            "variables_schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
        },
        headers=_auth(key),
    )
    template_id = publish.json()["id"]

    preview = await client.post(
        f"/api/v1/templates/{template_id}/preview", json={"payload": {}}, headers=_auth(key)
    )
    assert preview.status_code == 422


# --- Auth: docs/auditoria-seguranca-2026-08-29.md (item 1) / docs/prompts/autenticar-api-v1.md --

async def test_list_templates_without_api_key_returns_401(client, db_session):
    project_id, _ = await create_project_and_api_key(db_session)
    response = await client.get("/api/v1/templates/", params={"project_id": project_id})
    assert response.status_code == 401


async def test_publish_template_with_other_projects_key_returns_403(client, db_session):
    project_id, _ = await create_project_and_api_key(db_session, label="owner")
    _, other_key = await create_project_and_api_key(db_session, label="intruder")

    response = await client.post(
        "/api/v1/templates/",
        json={
            "project_id": project_id,
            "key": "welcome_email",
            "subject_template": "Hi",
            "html_template": "<p>Hi</p>",
        },
        headers=_auth(other_key),
    )
    assert response.status_code == 403


async def test_get_template_with_other_projects_key_returns_403(client, db_session):
    project_id, key = await create_project_and_api_key(db_session, label="owner")
    _, other_key = await create_project_and_api_key(db_session, label="intruder")

    publish = await client.post(
        "/api/v1/templates/",
        json={
            "project_id": project_id,
            "key": "welcome_email",
            "subject_template": "Hi",
            "html_template": "<p>Hi</p>",
        },
        headers=_auth(key),
    )
    template_id = publish.json()["id"]

    response = await client.get(f"/api/v1/templates/{template_id}", headers=_auth(other_key))
    assert response.status_code == 403


def _body(project_id: str, **overrides) -> dict:
    return {
        "project_id": project_id,
        "key": "welcome_email",
        "subject_template": "Hi {{ name }}",
        "html_template": "<p>{{ name }}</p>",
        **overrides,
    }


async def test_publish_rejects_invalid_jinja_and_keeps_previous_version_active(
    client, db_session
):
    project_id, key = await create_project_and_api_key(db_session)
    good = await client.post("/api/v1/templates/", json=_body(project_id), headers=_auth(key))
    assert good.status_code == 201

    bad = await client.post(
        "/api/v1/templates/",
        json=_body(project_id, subject_template="Hi {{ name"),
        headers=_auth(key),
    )

    assert bad.status_code == 422
    assert "subject" in bad.json()["detail"]
    listed = await client.get(
        f"/api/v1/templates/?project_id={project_id}", headers=_auth(key)
    )
    assert [(t["version"], t["is_active"]) for t in listed.json()] == [(1, True)]


async def test_publish_rejects_invalid_html_jinja(client, db_session):
    project_id, key = await create_project_and_api_key(db_session)
    response = await client.post(
        "/api/v1/templates/",
        json=_body(project_id, html_template="{% if x %}<p>unclosed"),
        headers=_auth(key),
    )
    assert response.status_code == 422
    assert "html" in response.json()["detail"]


async def test_publish_rejects_invalid_variables_schema(client, db_session):
    project_id, key = await create_project_and_api_key(db_session)
    response = await client.post(
        "/api/v1/templates/",
        json=_body(project_id, variables_schema={"type": "not-a-type"}),
        headers=_auth(key),
    )
    assert response.status_code == 422
    assert "variables_schema" in response.json()["detail"]


async def test_publish_rejects_bad_template_key(client, db_session):
    project_id, key = await create_project_and_api_key(db_session)
    response = await client.post(
        "/api/v1/templates/", json=_body(project_id, key="has space"), headers=_auth(key)
    )
    assert response.status_code == 422


async def test_activate_rolls_back_to_an_older_version(client, db_session):
    project_id, key = await create_project_and_api_key(db_session)
    v1 = (
        await client.post("/api/v1/templates/", json=_body(project_id), headers=_auth(key))
    ).json()
    await client.post(
        "/api/v1/templates/",
        json=_body(project_id, subject_template="v2 {{ name }}"),
        headers=_auth(key),
    )

    response = await client.post(f"/api/v1/templates/{v1['id']}/activate", headers=_auth(key))

    assert response.status_code == 200
    assert response.json()["is_active"] is True
    listed = await client.get(f"/api/v1/templates/?project_id={project_id}", headers=_auth(key))
    assert {t["version"]: t["is_active"] for t in listed.json()} == {1: True, 2: False}


async def test_activate_template_of_another_project_is_forbidden(client, db_session):
    project_id, _ = await create_project_and_api_key(db_session, label="a")
    _, other_key = await create_project_and_api_key(db_session, label="b")
    created = await TemplateService(TemplateRepository(db_session)).publish(
        TemplateCreate(
            project_id=uuid.UUID(project_id),
            key="k",
            subject_template="s",
            html_template="h",
        )
    )

    response = await client.post(
        f"/api/v1/templates/{created.id}/activate", headers=_auth(other_key)
    )

    assert response.status_code == 403
