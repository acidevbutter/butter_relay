import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import all_models  # noqa: F401  -- populates Base.metadata
from app.db.base import Base
from app.db.session import get_db_session
from app.main import app

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _no_real_queue_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Project creation broadcasts a `add_consumer` control message to Celery's
    real Redis broker (see app.worker.queues.add_consumer_for_project). There's
    no broker in the test environment, and each failed connection attempt is
    slow (kombu retries before giving up) -- stub it out so project-creation
    tests stay fast and don't depend on a running broker.
    """
    from celery.app.control import Control

    monkeypatch.setattr(Control, "add_consumer", lambda self, *args, **kwargs: None)


@pytest.fixture(autouse=True)
def _in_memory_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Redis in tests: count API-key requests in memory (DVB-308)."""
    from app.core import rate_limit

    monkeypatch.setattr(rate_limit, "backend", rate_limit.MemoryBackend())


@pytest.fixture
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession):
    async def _override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = _override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def admin_client(client: AsyncClient, db_session: AsyncSession) -> AsyncClient:
    """An `client` already logged in as an admin (session cookie set).

    /api/v1/projects/* and the /admin/* dashboard require an admin session --
    see docs/prompts/autenticar-api-v1.md. httpx.AsyncClient keeps a cookie jar,
    so the session cookie set by /admin/login is reused by later requests made
    with this same client.
    """
    from app.auth.repository import AdminUserRepository
    from app.auth.service import AuthService

    service = AuthService(AdminUserRepository(db_session))
    await service.register("admin@devbutter.com", "supersecret")

    response = await client.post(
        "/admin/login",
        data={"email": "admin@devbutter.com", "password": "supersecret"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


async def create_project_and_api_key(
    db_session: AsyncSession, label: str = "test-key", external: bool = False
) -> tuple[str, str]:
    """Creates a project directly through the service layer (bypassing HTTP, same
    pattern as `admin_client` registering an admin user directly) and mints an API
    key for it. Returns (project_id, plaintext_key).
    """
    from app.api_keys.repository import ApiKeyRepository
    from app.api_keys.service import ApiKeyService
    from app.projects.repository import ProjectRepository
    from app.projects.schemas import ProjectCreate
    from app.projects.service import ProjectService

    project_service = ProjectService(ProjectRepository(db_session))
    project = await project_service.create(
        ProjectCreate(
            name="Crate Diggr",
            slug=f"crate-diggr-{label}",
            external=external,
            default_sender_email="noreply@devbutter.com",
            default_sender_name="DevButter",
        )
    )
    api_key_service = ApiKeyService(ApiKeyRepository(db_session), ProjectRepository(db_session))
    _, plaintext_key = await api_key_service.create(project.id, label)
    return str(project.id), plaintext_key
