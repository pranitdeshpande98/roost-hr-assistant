"""Application settings, loaded from environment variables and the .env file."""

from functools import lru_cache
from typing import Literal, Self
from urllib.parse import quote

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The value shipped in .env.example. Allowed locally, rejected in production.
PLACEHOLDER_PASSWORD = "change-me-local-only"  # noqa: S105 - known placeholder, not a real secret

Environment = Literal["local", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Typed application configuration.

    Values come from environment variables first, then the .env file.
    Field names map to upper-case variable names (postgres_user -> POSTGRES_USER).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env may hold variables for other tools (e.g. Docker)
    )

    environment: Environment = "local"
    log_level: LogLevel = "INFO"

    # Option A: one full connection string (typical for hosted Postgres)
    database_url: SecretStr | None = None

    # Option B: individual parts (local Docker database)
    postgres_user: str = "roost"
    postgres_password: SecretStr | None = None
    postgres_db: str = "roost"
    postgres_host: str = "127.0.0.1"
    postgres_host_port: int = Field(default=5432, ge=1, le=65535)

    @model_validator(mode="after")
    def _check_database_config(self) -> Self:
        if self.database_url is None and self.postgres_password is None:
            raise ValueError("Set DATABASE_URL, or POSTGRES_PASSWORD for a local database.")
        if (
            self.environment == "production"
            and self.postgres_password is not None
            and self.postgres_password.get_secret_value() == PLACEHOLDER_PASSWORD
        ):
            raise ValueError("The placeholder Postgres password cannot be used in production.")
        return self

    @property
    def resolved_database_url(self) -> SecretStr:
        """SQLAlchemy connection URL. DATABASE_URL wins over the POSTGRES_* parts."""
        if self.database_url is not None:
            return self.database_url

        password = self.postgres_password
        if password is None:  # pragma: no cover - prevented by _check_database_config
            raise RuntimeError("No database password configured.")

        user = quote(self.postgres_user, safe="")
        secret = quote(password.get_secret_value(), safe="")
        return SecretStr(
            f"postgresql+psycopg://{user}:{secret}"
            f"@{self.postgres_host}:{self.postgres_host_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return the application settings, loaded once and cached."""
    return Settings()
