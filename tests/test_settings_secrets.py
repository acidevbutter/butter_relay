import pytest
from pydantic import ValidationError

from app.settings.config import Settings


def test_development_allows_default_secrets() -> None:
    settings = Settings(_env_file=None, environment="development")
    assert settings.session_secret == "dev-only-insecure-secret-change-me"


@pytest.mark.parametrize("environment", ["staging", "preprod", "production"])
def test_non_dev_rejects_default_session_secret(environment: str) -> None:
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        Settings(
            _env_file=None,
            environment=environment,
            database_password="real-password",
            brevo_api_key="key",
        )


def test_non_dev_rejects_default_database_password() -> None:
    with pytest.raises(ValidationError, match="DATABASE_PASSWORD"):
        Settings(
            _env_file=None,
            environment="staging",
            session_secret="real-secret",
            brevo_api_key="key",
        )


def _prod(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "environment": "production",
        "session_secret": "real-secret",
        "database_password": "real-password",
        "relay_secrets_keys": "k" * 43 + "=",
        # CI exports DEBUG=true for the admin-cookie tests; production settings here
        # must not inherit it.
        "debug": False,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_non_dev_allows_empty_shared_brevo_key() -> None:
    """DVB-206: projects may carry their own Brevo key; the shared one is optional."""
    assert _prod(brevo_api_key="").brevo_api_key == ""


@pytest.mark.parametrize("environment", ["staging", "preprod", "production"])
def test_non_dev_rejects_debug(environment: str) -> None:
    with pytest.raises(ValidationError, match="DEBUG must be false"):
        _prod(environment=environment, debug=True)


def test_development_allows_debug() -> None:
    assert Settings(_env_file=None, environment="development", debug=True).debug is True


def test_non_dev_rejects_missing_secrets_keys() -> None:
    with pytest.raises(ValidationError, match="RELAY_SECRETS_KEYS"):
        _prod(relay_secrets_keys="")


def test_non_dev_valid_config_passes() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        session_secret="real-secret",
        database_password="real-password",
        brevo_api_key="key",
        relay_secrets_keys="k" * 43 + "=",
        debug=False,
    )
    assert settings.session_secret == "real-secret"
