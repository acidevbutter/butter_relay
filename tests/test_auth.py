from app.auth.repository import AdminUserRepository
from app.auth.service import AuthService


async def test_login_with_correct_credentials_redirects_to_dashboard(client, db_session):
    service = AuthService(AdminUserRepository(db_session))
    await service.register("admin@devbutter.com", "supersecret")

    response = await client.post(
        "/admin/login",
        data={"email": "admin@devbutter.com", "password": "supersecret"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


async def test_login_with_wrong_password_returns_401(client, db_session):
    service = AuthService(AdminUserRepository(db_session))
    await service.register("admin@devbutter.com", "supersecret")

    response = await client.post(
        "/admin/login", data={"email": "admin@devbutter.com", "password": "wrong"}
    )
    assert response.status_code == 401


async def test_dashboard_without_session_redirects_to_login(client):
    response = await client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


async def test_register_duplicate_email_returns_409(client, db_session):
    service = AuthService(AdminUserRepository(db_session))
    await service.register("admin@devbutter.com", "supersecret")

    from app.core.exceptions import ConflictError

    try:
        await service.register("admin@devbutter.com", "other-password")
        raised = False
    except ConflictError:
        raised = True
    assert raised
