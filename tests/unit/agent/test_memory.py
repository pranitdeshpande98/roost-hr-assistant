"""Tests for roost.agent.memory that need no database."""

from roost.agent.memory import SAFE_CHECKPOINT_TYPES, psycopg_url


def test_psycopg_url_drops_the_sqlalchemy_driver_name() -> None:
    url = "postgresql+psycopg://roost:pw@127.0.0.1:5433/roost"

    assert psycopg_url(url) == "postgresql://roost:pw@127.0.0.1:5433/roost"


def test_plain_urls_are_unchanged() -> None:
    assert psycopg_url("postgresql://h/db") == "postgresql://h/db"


def test_every_type_stored_in_agent_state_is_allowlisted() -> None:
    assert set(SAFE_CHECKPOINT_TYPES) == {
        ("roost.llm.types", "ChatMessage"),
        ("roost.llm.types", "ToolCall"),
        ("roost.core.enums", "Language"),
        ("roost.core.enums", "Channel"),
    }
