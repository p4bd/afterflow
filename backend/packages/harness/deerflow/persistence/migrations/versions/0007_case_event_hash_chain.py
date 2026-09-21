"""Tamper-evident audit chain: prev_hash/event_hash on case_events.

Revision ID: 0007_case_event_hash_chain
Revises: 0006_action_approvers
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0007_case_event_hash_chain"
down_revision: str | Sequence[str] | None = "0006_action_approvers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column("case_events", sa.Column("prev_hash", sa.String(length=64), nullable=False, server_default=""))
    safe_add_column("case_events", sa.Column("event_hash", sa.String(length=64), nullable=False, server_default=""))


def downgrade() -> None:
    safe_drop_column("case_events", "event_hash")
    safe_drop_column("case_events", "prev_hash")
