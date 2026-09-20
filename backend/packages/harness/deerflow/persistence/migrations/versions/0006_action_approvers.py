"""Four-eyes approval: approvers_required + approver_ids on action_requests.

Revision ID: 0006_action_approvers
Revises: 0005_action_active_partial_index
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_action_approvers"
down_revision: str | Sequence[str] | None = "0005_action_active_partial_index"
# Parallel branch with 0006_after_sales_idempotency: four-eyes approval
# columns vs. idempotency cache. Both 0006_* must complete before 0008
# runs (see depends_on on 0008_action_reserved, which gates 0008 on both
# 0007_* revisions). ``branch_labels`` is informational and labels this
# head so the parallel structure is visible in ``alembic history``.
branch_labels: str | Sequence[str] | None = "after_sales_approvers"
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("action_requests"):
        return
    op.add_column("action_requests", sa.Column("approvers_required", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("action_requests", sa.Column("approver_ids", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("action_requests", "approver_ids")
    op.drop_column("action_requests", "approvers_required")
