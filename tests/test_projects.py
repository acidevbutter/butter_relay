PROJECT_PAYLOAD = {
    "name": "Crate Diggr",
    "slug": "crate-diggr",
    "default_sender_email": "noreply@devbutter.com",
    "default_sender_name": "DevButter",
}


async def test_create_project(admin_client):
    response = await admin_client.post("/api/v1/projects/", json=PROJECT_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert body["slug"] == "crate-diggr"
    assert body["external"] is False
    assert body["queue_name"] == "relay-crate-diggr"


async def test_create_external_project_sets_external_flag(admin_client):
    response = await admin_client.post(
        "/api/v1/projects/", json={**PROJECT_PAYLOAD, "slug": "external-co", "external": True}
    )
    assert response.status_code == 201
    assert response.json()["external"] is True


async def test_create_project_with_invalid_slug_returns_422(admin_client):
    response = await admin_client.post(
        "/api/v1/projects/", json={**PROJECT_PAYLOAD, "slug": "Not A Valid Slug!"}
    )
    assert response.status_code == 422


async def test_create_project_duplicate_slug_returns_409(admin_client):
    first = await admin_client.post("/api/v1/projects/", json=PROJECT_PAYLOAD)
    assert first.status_code == 201

    second = await admin_client.post("/api/v1/projects/", json=PROJECT_PAYLOAD)
    assert second.status_code == 409


async def test_get_missing_project_returns_404(admin_client):
    response = await admin_client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


# --- Auth: docs/auditoria-seguranca-2026-08-29.md (item 1) / docs/prompts/autenticar-api-v1.md --

async def test_list_projects_without_admin_session_returns_401(client):
    response = await client.get("/api/v1/projects/")
    assert response.status_code == 401


async def test_create_project_without_admin_session_returns_401(client):
    response = await client.post("/api/v1/projects/", json=PROJECT_PAYLOAD)
    assert response.status_code == 401
