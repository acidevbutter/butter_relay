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
