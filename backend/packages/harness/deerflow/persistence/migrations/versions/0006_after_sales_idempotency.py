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

from deerflow.persistence.migrations._helpers import safe_create_index, safe_create_table, safe_drop_table

revision: str = "0006_after_sales_idempotency"
down_revision: str | Sequence[str] | None = "0005_action_active_partial_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_create_table(
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
    safe_create_index("ix_idempotency_expires_at", "after_sales_idempotency", ["expires_at"])


def downgrade() -> None:
    safe_drop_table("after_sales_idempotency")
