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

from deerflow.persistence.migrations._helpers import safe_create_index, safe_create_table, safe_drop_table

revision: str = "0007_tool_call_audit"
down_revision: str | Sequence[str] | None = "0006_after_sales_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_create_table(
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
    safe_create_index("ix_tool_audit_case_recorded", "after_sales_tool_audit", ["case_id", "recorded_at"])


def downgrade() -> None:
    safe_drop_table("after_sales_tool_audit")
