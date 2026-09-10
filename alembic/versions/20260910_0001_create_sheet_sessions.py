"""Create sheet sessions table.

Revision ID: 20260910_0001
Revises:
Create Date: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260910_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sheet_sessions",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(),
            nullable=False,
        ),
        sa.Column("session_name", sa.String(length=255), nullable=False),
        sa.Column("spreadsheet_link", sa.Text(), nullable=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sheet_sessions"),
        sa.UniqueConstraint(
            "user_id",
            "session_name",
            name="uq_sheet_sessions_user_id_session_name",
        ),
    )


def downgrade() -> None:
    op.drop_table("sheet_sessions")
