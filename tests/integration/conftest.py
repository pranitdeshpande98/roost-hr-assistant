"""Fixtures for integration tests against a real Postgres database.

A fresh database named roost_test is created at the start of each test run
and migrated with Alembic, so the migrations themselves are tested too. Each
test runs inside a transaction that is rolled back afterwards, so tests never
see each other's data.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from roost.core.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_DB_NAME = "roost_test"


def _admin_and_test_urls() -> tuple[str, str]:
    """URLs for the 'postgres' maintenance database and the roost_test database."""
    base = make_url(get_settings().resolved_database_url.get_secret_value())
    admin = base.set(database="postgres").render_as_string(hide_password=False)
    test = base.set(database=TEST_DB_NAME).render_as_string(hide_password=False)
    return admin, test


def _recreate_test_database(admin_url: str) -> None:
    # CREATE/DROP DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text("DROP DATABASE IF EXISTS roost_test WITH (FORCE)"))
            connection.execute(text("CREATE DATABASE roost_test"))
    finally:
        admin.dispose()


def _migrate(test_url: str) -> None:
    """Run every Alembic migration against the test database."""
    config = Config()  # no ini file, so Alembic leaves pytest's logging alone
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url  # env.py reads the URL from settings
    get_settings.cache_clear()
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """An engine connected to a freshly migrated roost_test database."""
    admin_url, test_url = _admin_and_test_urls()
    try:
        _recreate_test_database(admin_url)
    except OperationalError as exc:
        if os.environ.get("REQUIRE_INTEGRATION") == "1":
            raise
        pytest.skip(f"Postgres is not reachable ({type(exc).__name__}). Run: docker compose up -d")

    _migrate(test_url)
    test_engine = create_engine(test_url)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    """A session whose changes are rolled back when the test ends."""
    connection = engine.connect()
    transaction = connection.begin()
    # session.commit() and session.rollback() inside a test only affect a savepoint.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
