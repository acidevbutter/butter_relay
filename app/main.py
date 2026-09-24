from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from app.admin.router import router as admin_router
from app.api_keys.router import router as api_keys_router
from app.auth.router import router as auth_router
from app.core.exceptions import register_exception_handlers
from app.core.metrics import configure_metrics, emit_metrics
from app.core.telemetry import configure_telemetry
from app.db.session import session_factory
from app.messages.router import router as messages_router
from app.projects.router import router as projects_router
from app.settings.config import settings
from app.settings.logging_config import setup_logging
from app.settings.middleware import register_middleware
from app.templates_.router import router as templates_router

setup_logging(settings.log_level)
configure_metrics()

app = FastAPI(title="Butter Relay", description="DevButter company-wide email dispatch service")
configure_telemetry(app)
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
        await emit_metrics([("HealthCheckFailure", 1, "Count")])
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/admin")
