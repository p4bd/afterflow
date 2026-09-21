"""Knowledge-layer health singleton row.

Revision ID: 0010_after_sales_knowledge_health
Revises: 0009_case_event_seq
Create Date: 2026-09-03

The ``after_sales_knowledge_health`` table holds the single row (id=1)
that backs ``SqlKnowledgeHealthRecorder``: process-wide aggregate
counters (attempt_count, fallback_count, consecutive_failures,
last_error, last_attempt_at) that survive process restart. Health is a
single number, not a per-event log; per-attempt audit lives in the
case-events chain.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_create_table, safe_drop_table

revision: str = "0010_after_sales_knowledge_health"
down_revision: str | Sequence[str] | None = "0009_case_event_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_create_table(
        "after_sales_knowledge_health",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fallback_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    safe_drop_table("after_sales_knowledge_health")
