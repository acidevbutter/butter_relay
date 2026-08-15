from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    debug: bool = False
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://relay:relay@db:5432/relay"

    aws_region: str = "us-east-1"
    sqs_queue_url: str = ""
    # Set only for local dev, pointed at the ElasticMQ container. Left unset on AWS
    # so boto3 talks to real SQS.
    sqs_endpoint_url: str | None = None
    sqs_wait_time_seconds: int = 20
    sqs_max_messages: int = 10
    sqs_visibility_timeout: int = 60

    brevo_api_key: str = ""

    session_secret: str = "dev-only-insecure-secret-change-me"
    session_cookie_name: str = "butter_relay_session"


settings = Settings()
