import pytest
from pydantic import ValidationError

from app.settings.config import Settings


def test_assembles_asyncpg_url_and_quotes_password() -> None:
    settings = Settings(
        _env_file=None,
        database_host="db.example",
        database_port=5432,
        database_user="app user",
        database_password="p@ss:w/rd",
        database_name="relay",
    )
    assert settings.database_url == (
        "postgresql+asyncpg://app%20user:p%40ss%3Aw%2Frd@db.example:5432/relay"
    )


def test_rejects_database_url_outside_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://legacy")
    with pytest.raises(ValidationError, match="DATABASE_URL is no longer read"):
        Settings(_env_file=None, environment="staging")
