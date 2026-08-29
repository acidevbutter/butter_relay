"""Minimal auth coverage for /api/v1/messages/* added by
docs/prompts/autenticar-api-v1.md (see docs/auditoria-seguranca-2026-08-29.md item 1).
Full coverage of this module (payload rendering, resend, etc.) is tracked in
docs/prompts/cobertura-testes-messages-api-keys.md.
"""

from app.messages.models import Message
from tests.conftest import create_project_and_api_key


def _auth(plaintext_key: str) -> dict:
    return {"Authorization": f"Bearer {plaintext_key}"}


async def test_list_messages_without_api_key_returns_401(client):
    response = await client.get("/api/v1/messages/")
    assert response.status_code == 401


async def test_list_messages_only_returns_own_project(client, db_session):
    project_id, key = await create_project_and_api_key(db_session, label="mine")
    other_project_id, _ = await create_project_and_api_key(db_session, label="other")

    db_session.add(Message(project_id=project_id, to_email="a@example.com"))
    db_session.add(Message(project_id=other_project_id, to_email="b@example.com"))
    await db_session.commit()

    response = await client.get("/api/v1/messages/", headers=_auth(key))
    assert response.status_code == 200
    bodies = response.json()
    assert len(bodies) == 1
    assert bodies[0]["project_id"] == project_id


async def test_get_message_from_other_project_returns_403(client, db_session):
    project_id, key = await create_project_and_api_key(db_session, label="mine")
    other_project_id, other_key = await create_project_and_api_key(db_session, label="other")

    message = Message(project_id=other_project_id, to_email="b@example.com")
    db_session.add(message)
    await db_session.commit()
    await db_session.refresh(message)

    response = await client.get(f"/api/v1/messages/{message.id}", headers=_auth(key))
    assert response.status_code == 403

    same_project = await client.get(f"/api/v1/messages/{message.id}", headers=_auth(other_key))
    assert same_project.status_code == 200
