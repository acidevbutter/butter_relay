import os
from typing import Literal
from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Celery broker + result backend. Shared Redis instance, but each app uses
    # its own queue name to avoid collisions -- see devbutter_backend's
    # RELAY_QUEUE_NAME, which must match this one for jobs to be picked up.
    redis_url: str = ""
    relay_queue_name: str = "relay-shared"

    brevo_api_key: str = ""
    allowed_recipient_domains: str = ""

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
        if not self.brevo_api_key:
            raise ValueError("BREVO_API_KEY must be set outside development")
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
