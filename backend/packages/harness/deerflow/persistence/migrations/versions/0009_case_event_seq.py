"""Monotonic seq on case_events for deterministic chain ordering.

Revision ID: 0009_case_event_seq
Revises: 0008_action_reserved
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_case_event_seq"
down_revision: str | Sequence[str] | None = "0008_action_reserved"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("case_events"):
        return
    op.add_column("case_events", sa.Column("seq", sa.Integer(), nullable=False, autoincrement=True))
    op.create_index("ix_case_events_seq", "case_events", ["seq"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_case_events_seq", table_name="case_events")
    op.drop_column("case_events", "seq")
