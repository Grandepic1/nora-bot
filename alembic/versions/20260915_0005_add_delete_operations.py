"""Allow confirmed row and sheet deletion actions.

Revision ID: 20260915_0005
Revises: 20260914_0004
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260915_0005"
down_revision: str | None = "20260914_0004"
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
        "'create_sheet', 'rename_sheet', 'delete_row', 'delete_sheet')",
    )


def downgrade() -> None:
    delete_action_exists = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM pending_sheet_actions "
            "WHERE operation IN ('delete_row', 'delete_sheet')"
            ")"
        )
    )
    if delete_action_exists:
        raise RuntimeError(
            "Cannot downgrade while delete action audit records exist"
        )
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
