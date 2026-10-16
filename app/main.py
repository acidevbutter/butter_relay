import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from app.admin.router import router as admin_router
from app.api_keys.router import router as api_keys_router
from app.auth.router import router as auth_router
from app.core.exceptions import register_exception_handlers
from app.db.session import session_factory
from app.messages.router import router as messages_router
from app.projects.repository import ProjectRepository
from app.projects.router import router as projects_router
from app.projects.service import ProjectService
from app.settings.config import settings
from app.settings.logging_config import setup_logging
from app.settings.middleware import register_middleware
from app.templates_.router import router as templates_router

setup_logging(settings.log_level)
logger = logging.getLogger(__name__)

async def check_delivery_configuration() -> list[str]:
    """Startup check (DVB-206): the shared BREVO_API_KEY is only required when an active
    Brevo project has no key of its own. Logged as an error instead of refusing to boot,
    so a missing key never takes the admin (where it can be fixed) down with it."""
    if settings.brevo_api_key:
        return []
    try:
        async with session_factory() as session:
            projects = await ProjectService(
                ProjectRepository(session)
            ).projects_needing_shared_brevo_key()
    except Exception:
        logger.warning("could not check Brevo configuration at startup", exc_info=True)
        return []
    slugs = sorted(p.slug for p in projects)
    if slugs:
        logger.error(
            "BREVO_API_KEY is empty but these active Brevo projects have no key of their "
            "own; their e-mail will fail: %s",
            ", ".join(slugs),
            extra={"event": "brevo_key_missing"},
        )
    return slugs


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await check_delivery_configuration()
    yield


app = FastAPI(
    title="Butter Relay",
    description="DevButter company-wide email dispatch service",
    lifespan=lifespan,
)
register_middleware(app)
register_exception_handlers(app)

app.include_router(projects_router)
app.include_router(api_keys_router)
app.include_router(templates_router)
app.include_router(messages_router)
app.include_router(auth_router)
app.include_router(admin_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["meta"])
async def readiness() -> dict[str, str]:
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error(
            "readiness check failed: database unavailable",
            exc_info=True,
            extra={"event": "readiness_failed"},
        )
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/admin")
