"""AfterFlow service cases, action requests, and audit events.

Revision ID: 0004_after_sales
Revises: 0003_scheduled_tasks
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_create_index, safe_create_table, safe_drop_table

revision: str = "0004_after_sales"
down_revision: str | Sequence[str] | None = "0003_scheduled_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Each table is guarded independently. An early ``return`` on
    # ``service_cases`` (the previous shape of this revision) would skip
    # ``action_requests`` and ``case_events`` entirely on a DB that happened to
    # have only the first of the three, leaving the schema half-built while
    # alembic still stamped this revision as applied.
    safe_create_table(
        "service_cases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("issue_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("decision_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("user_id", "thread_id", "order_id", "status"):
        safe_create_index(f"ix_service_cases_{column}", "service_cases", [column])

    safe_create_table(
        "action_requests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("required_role", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_transaction_id", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["service_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for column in ("case_id", "status", "expires_at"):
        safe_create_index(f"ix_action_requests_{column}", "action_requests", [column])

    safe_create_table(
        "case_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("event_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["service_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("case_id", "event_type"):
        safe_create_index(f"ix_case_events_{column}", "case_events", [column])


def downgrade() -> None:
    safe_drop_table("case_events")
    safe_drop_table("action_requests")
    safe_drop_table("service_cases")
