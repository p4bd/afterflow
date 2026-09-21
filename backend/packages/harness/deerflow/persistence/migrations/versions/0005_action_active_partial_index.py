"""DB-level guarantee: a case has at most one active action.

Revision ID: 0005_action_active_partial_index
Revises: 0004_after_sales
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_action_active_partial_index"
down_revision: str | Sequence[str] | None = "0004_after_sales"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("action_requests"):
        return
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_action_one_active_per_case ON action_requests (case_id) WHERE status IN ('pending_approval', 'approved')")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_action_one_active_per_case")
