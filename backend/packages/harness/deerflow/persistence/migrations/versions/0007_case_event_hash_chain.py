"""Tamper-evident audit chain: prev_hash/event_hash on case_events.

Revision ID: 0007_case_event_hash_chain
Revises: 0006_action_approvers
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_case_event_hash_chain"
down_revision: str | Sequence[str] | None = "0006_action_approvers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("case_events"):
        return
    op.add_column("case_events", sa.Column("prev_hash", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("case_events", sa.Column("event_hash", sa.String(length=64), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("case_events", "event_hash")
    op.drop_column("case_events", "prev_hash")
