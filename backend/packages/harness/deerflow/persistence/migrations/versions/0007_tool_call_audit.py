"""Tool-call audit for AfterFlow.

Revision ID: 0007_tool_call_audit
Revises: 0006_after_sales_idempotency
Create Date: 2026-08-31

One row per tool invocation. Combined with `case_events` this gives the
auditor the full call chain: which tools the agent called, what was
returned, what the agent did with the response, and what side effects
followed. Indexed by (case_id, recorded_at) for ordered replay.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_tool_call_audit"
down_revision: str | Sequence[str] | None = "0006_after_sales_idempotency"
# Parallel branch with 0007_case_event_hash_chain: tool-call audit table
# vs. hash-chain columns on case_events. 0008 depends on this revision
# (via ``depends_on``) so the chain does not fork into two permanent heads.
branch_labels: str | Sequence[str] | None = "after_sales_tool_audit"
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "after_sales_tool_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("user_role", sa.String(length=32), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_tool_audit_case_recorded", "after_sales_tool_audit", ["case_id", "recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_tool_audit_case_recorded", table_name="after_sales_tool_audit")
    op.drop_table("after_sales_tool_audit")
