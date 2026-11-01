"""create agent memory schema

LangGraph's checkpointer owns the tables inside this schema and migrates them itself
(roost.agent.memory). Alembic only owns the schema, so autogenerate never sees them.

Revision ID: 746d8ff137fb
Revises: eb2bb379f65c
Create Date: 2026-11-01

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "746d8ff137fb"
down_revision: str | Sequence[str] | None = "eb2bb379f65c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE SCHEMA IF NOT EXISTS agent_memory")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP SCHEMA IF EXISTS agent_memory CASCADE")
