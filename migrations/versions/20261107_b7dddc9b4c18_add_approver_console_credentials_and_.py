"""add approver console credentials and web sessions

kiosk_session becomes web_session, shared by the kiosk and the approver console, with an
`app` column. The audit log learns the password actions. Action lists are written out
here, not imported, so this migration never changes when the app code does.

Revision ID: b7dddc9b4c18
Revises: bdcc1783bebd
Create Date: 2026-11-07 15:21:29.115738

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7dddc9b4c18"
down_revision: str | Sequence[str] | None = "bdcc1783bebd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_ACTIONS = (
    "request_submitted",
    "request_changed",
    "request_cancelled",
    "approval_decided",
    "request_approved",
    "request_denied",
    "hr_summary_sent",
    "pin_set",
    "pin_changed",
    "pin_locked",
    "legal_hold_placed",
    "legal_hold_released",
)
NEW_ACTIONS = (*OLD_ACTIONS, "password_set", "password_changed", "password_locked")


def _action_check(actions: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_audit_log_auditaction"), "audit_log", type_="check")
    listed = ", ".join(f"'{a}'" for a in actions)
    op.create_check_constraint(
        op.f("ck_audit_log_auditaction"), "audit_log", f"action IN ({listed})"
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.rename_table("kiosk_session", "web_session")
    op.execute("ALTER INDEX pk_kiosk_session RENAME TO pk_web_session")
    op.execute("ALTER INDEX ix_kiosk_session_last_seen_at RENAME TO ix_web_session_last_seen_at")
    op.execute(
        "ALTER TABLE web_session RENAME CONSTRAINT fk_kiosk_session_employee_id_employee "
        "TO fk_web_session_employee_id_employee"
    )
    op.execute(
        "ALTER TABLE web_session RENAME CONSTRAINT ck_kiosk_session_language "
        "TO ck_web_session_language"
    )
    op.add_column(
        "web_session",
        sa.Column(
            "app",
            sa.Enum(
                "kiosk",
                "console",
                name="sessionapp",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="kiosk",
            nullable=False,
        ),
    )
    op.create_table(
        "console_credential",
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.Column("must_change", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "failed_attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "failed_attempts >= 0",
            name=op.f("ck_console_credential_failed_attempts_not_negative"),
        ),
        sa.CheckConstraint(
            "password_hash LIKE '$argon2id$%%'",
            name=op.f("ck_console_credential_password_hash_is_argon2id"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employee.id"],
            name=op.f("fk_console_credential_employee_id_employee"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("employee_id", name=op.f("pk_console_credential")),
    )
    _action_check(NEW_ACTIONS)


def downgrade() -> None:
    """Downgrade schema. Fails if the audit log already holds password actions: the log
    is permanent, so those rows cannot be removed to make room."""
    _action_check(OLD_ACTIONS)
    op.drop_table("console_credential")
    op.execute("DELETE FROM web_session WHERE app = 'console'")
    op.drop_constraint(op.f("ck_web_session_sessionapp"), "web_session", type_="check")
    op.drop_column("web_session", "app")
    op.execute(
        "ALTER TABLE web_session RENAME CONSTRAINT ck_web_session_language "
        "TO ck_kiosk_session_language"
    )
    op.execute(
        "ALTER TABLE web_session RENAME CONSTRAINT fk_web_session_employee_id_employee "
        "TO fk_kiosk_session_employee_id_employee"
    )
    op.execute("ALTER INDEX ix_web_session_last_seen_at RENAME TO ix_kiosk_session_last_seen_at")
    op.execute("ALTER INDEX pk_web_session RENAME TO pk_kiosk_session")
    op.rename_table("web_session", "kiosk_session")
