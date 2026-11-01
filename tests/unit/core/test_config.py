"""Unit tests for roost.core.config."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from roost.core.config import PLACEHOLDER_PASSWORD, Settings, get_settings

SETTINGS_ENV_VARS = (
    "ENVIRONMENT",
    "LOG_LEVEL",
    "DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_HOST_PORT",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove settings variables so the machine's environment cannot affect tests."""
    for name in SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def make_settings(**overrides: object) -> Settings:
    """Build Settings from explicit values only, ignoring any .env file."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_builds_database_url_from_postgres_parts() -> None:
    settings = make_settings(postgres_password="secret", postgres_host_port=5433)

    assert (
        settings.resolved_database_url.get_secret_value()
        == "postgresql+psycopg://roost:secret@127.0.0.1:5433/roost"
    )


def test_url_encodes_special_characters_in_password() -> None:
    settings = make_settings(postgres_password="p@ss:w/rd#1")

    assert "p%40ss%3Aw%2Frd%231@" in settings.resolved_database_url.get_secret_value()


def test_database_url_takes_precedence_over_parts() -> None:
    url = "postgresql+psycopg://app:pw@db.example.com:5432/roost?sslmode=require"
    settings = make_settings(database_url=url, postgres_password="ignored")

    assert settings.resolved_database_url.get_secret_value() == url


def test_requires_database_url_or_password() -> None:
    with pytest.raises(ValidationError, match="Set DATABASE_URL"):
        make_settings()


def test_production_rejects_placeholder_password() -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        make_settings(environment="production", postgres_password=PLACEHOLDER_PASSWORD)


def test_local_allows_placeholder_password() -> None:
    settings = make_settings(environment="local", postgres_password=PLACEHOLDER_PASSWORD)

    assert settings.environment == "local"


def test_secrets_are_hidden_when_printed() -> None:
    settings = make_settings(postgres_password="topsecret")

    assert "topsecret" not in repr(settings)
    assert "topsecret" not in str(settings.resolved_database_url)


@pytest.mark.parametrize("port", [0, 70000])
def test_rejects_invalid_port(port: int) -> None:
    with pytest.raises(ValidationError, match="postgres_host_port"):
        make_settings(postgres_password="secret", postgres_host_port=port)


def test_rejects_unknown_environment() -> None:
    with pytest.raises(ValidationError, match="environment"):
        make_settings(environment="dev", postgres_password="secret")


def test_reads_values_from_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "from-env")
    monkeypatch.setenv("POSTGRES_HOST_PORT", "5433")

    settings = Settings(_env_file=None)

    assert settings.environment == "test"
    assert settings.postgres_host_port == 5433
    assert settings.postgres_password is not None
    assert settings.postgres_password.get_secret_value() == "from-env"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # an empty folder, so no .env is found
    monkeypatch.setenv("POSTGRES_PASSWORD", "from-env")
    get_settings.cache_clear()

    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()  # don't leak this instance into other tests
