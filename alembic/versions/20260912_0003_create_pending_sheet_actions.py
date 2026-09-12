"""Create pending sheet actions table.

Revision ID: 20260912_0003
Revises: 20260910_0002
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0003"
down_revision: str | None = "20260910_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_sheet_actions",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("sheet_session_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("waha_session", sa.String(length=255), nullable=True),
        sa.Column("chat_id", sa.String(length=255), nullable=False),
        sa.Column("source_message_id", sa.String(length=255), nullable=True),
        sa.Column("spreadsheet_id", sa.String(length=255), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("preview", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "operation IN ('append_rows', 'update_row', "
            "'create_sheet', 'rename_sheet')",
            name="pending_sheet_actions_operation",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', "
            "'failed', 'expired', 'stale', 'conflict', 'cancelled')",
            name="pending_sheet_actions_status",
        ),
        sa.ForeignKeyConstraint(
            ["sheet_session_id"],
            ["sheet_sessions.id"],
            name=(
                "fk_pending_sheet_actions_sheet_session_id_sheet_sessions"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pending_sheet_actions"),
        sa.UniqueConstraint("code", name="uq_pending_sheet_actions_code"),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_pending_sheet_actions_token_hash",
        ),
    )
    op.create_index(
        "ix_pending_sheet_actions_expires_at",
        "pending_sheet_actions",
        ["expires_at"],
    )
    op.create_index(
        "ix_pending_sheet_actions_sheet_session_id",
        "pending_sheet_actions",
        ["sheet_session_id"],
    )
    op.create_index(
        "ix_pending_sheet_actions_user_id",
        "pending_sheet_actions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pending_sheet_actions_user_id",
        table_name="pending_sheet_actions",
    )
    op.drop_index(
        "ix_pending_sheet_actions_sheet_session_id",
        table_name="pending_sheet_actions",
    )
    op.drop_index(
        "ix_pending_sheet_actions_expires_at",
        table_name="pending_sheet_actions",
    )
    op.drop_table("pending_sheet_actions")
