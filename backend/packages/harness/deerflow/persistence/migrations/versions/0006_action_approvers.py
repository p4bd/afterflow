"""Four-eyes approval: approvers_required + approver_ids on action_requests.

Revision ID: 0006_action_approvers
Revises: 0005_action_active_partial_index
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0006_action_approvers"
down_revision: str | Sequence[str] | None = "0005_action_active_partial_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column("action_requests", sa.Column("approvers_required", sa.Integer(), nullable=False, server_default="1"))
    safe_add_column("action_requests", sa.Column("approver_ids", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    safe_drop_column("action_requests", "approver_ids")
    safe_drop_column("action_requests", "approvers_required")
