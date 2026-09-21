"""Monotonic seq on case_events for deterministic chain ordering.

Revision ID: 0009_case_event_seq
Revises: 0008_action_reserved
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_create_index, safe_drop_column

revision: str = "0009_case_event_seq"
down_revision: str | Sequence[str] | None = "0008_action_reserved"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column("case_events", sa.Column("seq", sa.Integer(), nullable=False, autoincrement=True))
    safe_create_index("ix_case_events_seq", "case_events", ["seq"], unique=True)


def downgrade() -> None:
    safe_drop_column("case_events", "seq")
