"""Alembic migration environment for Roost."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from roost.core.config import get_settings
from roost.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic compares this metadata to the database to detect schema changes.
target_metadata = Base.metadata


def _database_url() -> str:
    """Read the URL from Roost settings so secrets never live in alembic.ini."""
    return get_settings().resolved_database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Generate SQL scripts without connecting (alembic upgrade --sql)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and apply migrations."""
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
