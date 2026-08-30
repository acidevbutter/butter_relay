from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "staging", "preprod", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://relay:relay@shared-postgres:5432/relay"

    aws_region: str = "us-east-1"
    sqs_queue_url: str = ""
    # Set only for local dev, pointed at the ElasticMQ container. Left unset on AWS
    # so boto3 talks to real SQS.
    sqs_endpoint_url: str | None = None
    sqs_wait_time_seconds: int = 20
    sqs_max_messages: int = 10
    sqs_visibility_timeout: int = 60

    brevo_api_key: str = ""
    allowed_recipient_domains: str = ""

    session_secret: str = "dev-only-insecure-secret-change-me"
    session_cookie_name: str = "butter_relay_session"

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
