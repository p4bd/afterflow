"""Idempotency cache for AfterFlow write endpoints.

Revision ID: 0006_after_sales_idempotency
Revises: 0005_action_active_partial_index
Create Date: 2026-08-31

This table is the durable backing for the per-endpoint Idempotency-Key
contract (Stripe-style). A row exists when an endpoint has been called once
with a given key; subsequent calls replay the cached response. The unique
constraint on (key, endpoint) is the canonical race resolution: two
concurrent first-writers race, only one INSERT commits, the other catches
IntegrityError.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_after_sales_idempotency"
down_revision: str | Sequence[str] | None = "0005_action_active_partial_index"
# Parallel branch with 0006_action_approvers: idempotency cache table
# while the sibling migration adds four-eyes columns. Both 0006_* must
# complete before 0008 runs (see ``depends_on`` on 0008_action_reserved,
# which gates 0008 on this revision without changing its down_revision).
branch_labels: str | Sequence[str] | None = "after_sales_idempotency"
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "after_sales_idempotency",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key", "endpoint", name="uq_idempotency_key_endpoint"),
    )
    op.create_index("ix_idempotency_expires_at", "after_sales_idempotency", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_expires_at", table_name="after_sales_idempotency")
    op.drop_table("after_sales_idempotency")
