PROJECT_PAYLOAD = {
    "name": "Crate Diggr",
    "slug": "crate-diggr",
    "sqs_queue_url": "http://elasticmq:9324/000000000000/relay-shared",
    "default_sender_email": "noreply@devbutter.com",
    "default_sender_name": "DevButter",
}


async def _create_project(client) -> str:
    response = await client.post("/api/v1/projects/", json=PROJECT_PAYLOAD)
    return response.json()["id"]


async def test_publish_and_preview_template(client):
    project_id = await _create_project(client)

    publish = await client.post(
        "/api/v1/templates/",
        json={
            "project_id": project_id,
            "key": "welcome_email",
            "subject_template": "Welcome, {{ name }}!",
            "html_template": "<p>Hi {{ name }}</p>",
        },
    )
    assert publish.status_code == 201
    template_id = publish.json()["id"]
    assert publish.json()["version"] == 1

    preview = await client.post(
        f"/api/v1/templates/{template_id}/preview", json={"payload": {"name": "Marcos"}}
    )
    assert preview.status_code == 200
    assert preview.json() == {"subject": "Welcome, Marcos!", "html": "<p>Hi Marcos</p>"}


async def test_publishing_new_version_deactivates_prior(client):
    project_id = await _create_project(client)
    body = {
        "project_id": project_id,
        "key": "welcome_email",
        "subject_template": "v1 subject",
        "html_template": "<p>v1</p>",
    }
    v1 = await client.post("/api/v1/templates/", json=body)
    assert v1.json()["version"] == 1
    assert v1.json()["is_active"] is True

    body["subject_template"] = "v2 subject"
    v2 = await client.post("/api/v1/templates/", json=body)
    assert v2.json()["version"] == 2
    assert v2.json()["is_active"] is True

    listing = await client.get("/api/v1/templates/", params={"project_id": project_id})
    versions = {t["version"]: t["is_active"] for t in listing.json()}
    assert versions == {1: False, 2: True}


async def test_template_render_rejects_payload_violating_schema(client):
    project_id = await _create_project(client)
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
    )
    template_id = publish.json()["id"]

    preview = await client.post(f"/api/v1/templates/{template_id}/preview", json={"payload": {}})
    assert preview.status_code == 422
