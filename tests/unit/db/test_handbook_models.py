"""Unit tests for the handbook model definitions (no database needed)."""

from pgvector.sqlalchemy import Vector

from roost.core.enums import HandbookStatus
from roost.db.base import Base


def test_handbook_status_values_are_stable() -> None:
    assert [m.value for m in HandbookStatus] == ["draft", "published", "retired"]


def test_only_one_version_can_be_published() -> None:
    index = next(
        i
        for i in Base.metadata.tables["handbook_version"].indexes
        if i.name == "uq_handbook_version_single_published"
    )

    assert index.unique is True
    assert str(index.dialect_options["postgresql"]["where"]) == "status = 'published'"


def test_chunk_search_column_is_generated_and_indexed() -> None:
    table = Base.metadata.tables["handbook_chunk"]

    assert table.c.search_tsv.computed is not None
    assert any(
        i.name == "ix_handbook_chunk_search_tsv"
        and i.dialect_options["postgresql"]["using"] == "gin"
        for i in table.indexes
    )


def test_embeddings_have_no_fixed_dimension() -> None:
    column_type = Base.metadata.tables["chunk_embedding"].c.embedding.type

    assert isinstance(column_type, Vector)
    assert column_type.dim is None
