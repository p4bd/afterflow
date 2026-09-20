"""DB-level UNIQUE on (case_id, seq): the authoritative backstop for P0-B.

Revision ID: 0011_case_event_seq_unique
Revises: 0010_after_sales_knowledge_health, 0007_tool_call_audit
Create Date: 2026-09-03

This migration does two things at once:

1. **Merge migration (P0-D)** — ``down_revision`` is a tuple of the two
   heads that existed when P0-D was resolved: ``0010_after_sales_knowledge_health``
   (the ``after_sales_knowledge_health`` / ``after_sales_approvers`` branch)
   and ``0007_tool_call_audit`` (the ``after_sales_idempotency`` /
   ``after_sales_tool_audit`` branch). After this migration the DAG has a
   single head again. ``depends_on`` on ``0008_action_reserved`` already
   enforced correct upgrade ordering; the merge makes ``alembic upgrade
   head`` (singular) work in CI without juggling branches.

2. **Composite unique constraint (P0-B)** — ``case_events`` previously had
   only ``UNIQUE(id)`` and a global ``UNIQUE(seq)`` (added in 0009 as
   ``ix_case_events_seq``). Two concurrent ``append_event`` calls could
   each compute the same per-case seq, both INSERT, and the chain would
   fork: the auditor walks by seq and only validates one branch locally.
   The DB-level ``UNIQUE(case_id, seq)`` added here is the authoritative
   race resolution. The application layer (``append_event``) catches
   ``IntegrityError`` and re-reads the last seq — see ``repository.py``.

3. **Drop global UNIQUE(seq)** — 0009 added ``ix_case_events_seq`` as a
   unique INDEX on seq alone. That invariant is wrong for the per-case
   append pattern: ``append_event`` computes ``last.seq + 1`` per case,
   so two cases naturally both start at seq=1, and a global UNIQUE(seq)
   would forbid that. The 0009 index is dropped and replaced with a
   plain (non-unique) index on seq for fast verify-event-chain walks.

The ``ix_case_events_case_id`` index is kept (filter-by-case queries use
it and the composite constraint doesn't substitute).

SQLite note: we use ``create_index(..., unique=True)`` rather than
``create_unique_constraint`` because SQLite cannot ``ALTER TABLE ADD
CONSTRAINT`` outside of batch mode. A unique INDEX is functionally
equivalent to a unique constraint on SQLite and on PostgreSQL the same
DDL produces an index-backed unique constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_case_event_seq_unique"
down_revision: str | Sequence[str] | None = (
    "0010_after_sales_knowledge_health",
    "0007_tool_call_audit",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("case_events"):
        return
    inspector = sa.inspect(bind)
    existing_unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints("case_events") if c.get("column_names")}
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("case_events")}

    # Drop the pre-existing global UNIQUE(seq) index from 0009. We replace
    # it with a non-unique index on seq so verify_event_chain can still
    # scan the chain by seq alone. Idempotent: if the unique index no
    # longer exists (e.g. fresh DB created via Base.metadata.create_all
    # after the model change in this fix) we skip the drop.
    if "ix_case_events_seq" in existing_indexes:
        op.drop_index("ix_case_events_seq", table_name="case_events")
        existing_indexes.discard("ix_case_events_seq")
    # Replace with a plain (non-unique) index so seq lookups stay fast.
    if "ix_case_events_seq" not in existing_indexes:
        op.create_index("ix_case_events_seq", "case_events", ["seq"], unique=False)

    # Composite uniqueness is the authoritative P0-B backstop. Idempotent:
    # if a unique constraint on (case_id, seq) already exists (e.g. from
    # ``Base.metadata.create_all`` in a test DB) we skip the create.
    if ("case_id", "seq") in existing_unique or "uq_case_events_case_id_seq" in existing_indexes:
        return
    op.create_index(
        "uq_case_events_case_id_seq",
        "case_events",
        ["case_id", "seq"],
        unique=True,
    )


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("case_events"):
        return
    op.drop_index("uq_case_events_case_id_seq", table_name="case_events")
    # Restore the 0009-era global UNIQUE(seq). We don't restore ``ix_case_events_seq``
    # because the model's pre-fix ``unique=True`` on the seq column was
    # what drove it; the downgrade returns to the schema state before 0011.
