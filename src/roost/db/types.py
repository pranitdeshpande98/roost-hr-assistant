"""Reusable SQLAlchemy column types."""

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def str_enum(enum_cls: type[StrEnum]) -> SAEnum:
    """Store a StrEnum as VARCHAR plus a CHECK constraint.

    Native Postgres enum types are hard to alter in migrations; a CHECK
    constraint gives the same protection and is easy to change.
    """
    return SAEnum(
        enum_cls,
        name=enum_cls.__name__.lower(),
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )
