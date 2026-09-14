"""Allow coordinate-aware update cells actions.

Revision ID: 20260914_0004
Revises: 20260912_0003
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260914_0004"
down_revision: str | None = "20260912_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_pending_sheet_actions_pending_sheet_actions_operation"


def upgrade() -> None:
    op.drop_constraint(
        op.f(CONSTRAINT_NAME),
        "pending_sheet_actions",
        type_="check",
    )
    op.create_check_constraint(
        "pending_sheet_actions_operation",
        "pending_sheet_actions",
        "operation IN ('append_rows', 'update_row', 'update_cells', "
        "'create_sheet', 'rename_sheet')",
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM pending_sheet_actions WHERE operation = 'update_cells'")
    )
    op.drop_constraint(
        op.f(CONSTRAINT_NAME),
        "pending_sheet_actions",
        type_="check",
    )
    op.create_check_constraint(
        "pending_sheet_actions_operation",
        "pending_sheet_actions",
        "operation IN ('append_rows', 'update_row', 'create_sheet', 'rename_sheet')",
    )
