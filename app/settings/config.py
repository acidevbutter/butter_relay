import os
from typing import Literal
from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_NAME = "butter-relay"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "staging", "preprod", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"

    database_host: str = "shared-postgres"
    database_port: int = 5432
    database_user: str = "relay"
    database_password: str = "relay"
    database_name: str = "relay"

    # Celery broker/result backend. Same Redis instance the worker (celery -A
    # app.worker.celery_app worker) and the API share -- devbutter_backend and
    # selolibertaria_backend publish onto RELAY_QUEUE_NAME with their own
    # Celery client pointed at this same URL (same env var name as their own
    # settings, for consistency). Left pointing at the local docker-compose
    # `redis` service for dev.
    redis_url: str = "redis://redis:6379/0"
    relay_queue_name: str = "relay-shared"
    # Largest JSON payload a task may carry; bigger ones are rejected, not rendered (DVB-188).
    relay_max_payload_bytes: int = 64 * 1024
    # Requests per minute per API key (DVB-308); a project may override it. 0 disables.
    relay_default_rate_per_minute: int = 120
    # Legacy send_task(project_id, template_key, to, payload) straight into Redis trusts
    # the producer's project_id (H-02). Turn off once producers use the HTTP API.
    allow_legacy_send_task: bool = True
    # When set, /admin and /api/v1/admin respond only on these hosts (comma-separated);
    # other hosts only serve the public send API and /health (DVB-309).
    admin_allowed_hosts: str = ""
    # Payloads (rendered personal data) of finished messages and the recipient of
    # rejected tasks are cleared after this many days (DVB-208 / LGPD).
    message_body_retention_days: int = 30

    brevo_api_key: str = ""
    # Fernet keys (comma-separated, newest first) that encrypt per-project delivery
    # secrets at rest (DVB-306). Mandatory outside development.
    relay_secrets_keys: str = ""
    allowed_recipient_domains: str = ""

    # Only used when environment == "development" (see get_email_provider) --
    # points at the mailpit service in docker-compose.yml so local sends land
    # in a safe inbox instead of hitting the real Brevo API.
    smtp_host: str = "mailpit"
    smtp_port: int = 1025

    butter_blob_base_url: str = ""
    butter_blob_api_key: str = ""
    butter_blob_namespace: str = "nfse"
    butter_blob_timeout_seconds: float = 15.0

    session_secret: str = "dev-only-insecure-secret-change-me"
    session_cookie_name: str = "butter_relay_session"

    @property
    def database_url(self) -> str:
        user = quote(self.database_user, safe="")
        password = quote(self.database_password, safe="")
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )

    @model_validator(mode="after")
    def reject_legacy_database_url_outside_dev(self) -> "Settings":
        if self.environment != "development" and os.environ.get("DATABASE_URL"):
            raise ValueError(
                "DATABASE_URL is no longer read; set DATABASE_HOST, DATABASE_PORT, "
                "DATABASE_USER, DATABASE_PASSWORD and DATABASE_NAME"
            )
        return self

    @model_validator(mode="after")
    def reject_dev_secrets_outside_development(self) -> "Settings":
        if self.environment == "development":
            return self
        defaults = type(self).model_fields
        if self.session_secret == defaults["session_secret"].default:
            raise ValueError(
                "SESSION_SECRET must not be left at its development default outside development"
            )
        if self.database_password == defaults["database_password"].default:
            raise ValueError(
                "DATABASE_PASSWORD must not be left at its development default outside development"
            )
        # BREVO_API_KEY is no longer mandatory here: a project may carry its own Brevo
        # key (DVB-306). Projects that rely on the shared key are checked at startup
        # (app.main) and fail permanently at send time when it is missing (DVB-206).
        if self.debug:
            raise ValueError(
                "DEBUG must be false outside development (it disables secure session cookies)"
            )
        if not self.relay_secrets_keys.strip():
            raise ValueError("RELAY_SECRETS_KEYS must be set outside development")
        return self

    def is_recipient_allowed(self, email: str) -> bool:
        if self.environment in {"development", "production"}:
            return True
        allowed_domains = {
            domain.strip().lower()
            for domain in self.allowed_recipient_domains.split(",")
            if domain.strip()
        }
        return bool(allowed_domains) and email.rpartition("@")[2].lower() in allowed_domains


settings = Settings()
