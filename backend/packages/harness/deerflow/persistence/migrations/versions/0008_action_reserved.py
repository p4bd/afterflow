"""Track fund reservations on action_requests (for the expiry reaper).

Revision ID: 0008_action_reserved
Revises: 0007_case_event_hash_chain
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_action_reserved"
down_revision: str | Sequence[str] | None = "0007_case_event_hash_chain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("action_requests"):
        return
    op.add_column("action_requests", sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("action_requests", "reserved")
