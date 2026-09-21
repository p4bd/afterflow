"""Track fund reservations on action_requests (for the expiry reaper).

Revision ID: 0008_action_reserved
Revises: 0007_case_event_hash_chain
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0008_action_reserved"
down_revision: str | Sequence[str] | None = "0007_case_event_hash_chain"
# The 0006_*/0007_* pair runs as two parallel branches (four-eyes columns and
# the idempotency cache vs. the hash-chain columns and the tool-call audit
# table). They are re-joined by the merge revision 0011_case_event_seq_unique,
# whose ``down_revision`` is the tuple ``(0010_..., 0007_tool_call_audit)`` --
# that tuple is what collapses the DAG back to a single head, and alembic's
# own ancestors-before-descendants ordering is what guarantees every branch
# completes before the merge. No ``depends_on`` is needed here.
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column("action_requests", sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    safe_drop_column("action_requests", "reserved")
