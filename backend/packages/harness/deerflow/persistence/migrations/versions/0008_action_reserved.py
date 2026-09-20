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
# Parallel-branch merge: 0007_tool_call_audit is a sibling head alongside
# 0007_case_event_hash_chain. Declaring ``depends_on`` (without changing
# ``down_revision``, which is committed history) forces 0008 to wait for
# both 0007_* migrations to complete, collapsing the heads back into a
# single linear chain: 0007_case_event_hash_chain + 0007_tool_call_audit
# → 0008 → 0009 → 0010_*. Same pattern applies to the 0006_* pair.
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = "0007_tool_call_audit"


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("action_requests"):
        return
    op.add_column("action_requests", sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("action_requests", "reserved")
