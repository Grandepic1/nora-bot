"""Create active sheet sessions table.

Revision ID: 20260910_0002
Revises: 20260910_0001
Create Date: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260910_0002"
down_revision: str | None = "20260910_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "active_sheet_sessions",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("sheet_session_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sheet_session_id"],
            ["sheet_sessions.id"],
            name=(
                "fk_active_sheet_sessions_sheet_session_id_sheet_sessions"
            ),
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_active_sheet_sessions"),
    )


def downgrade() -> None:
    op.drop_table("active_sheet_sessions")
