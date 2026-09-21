"""ORM row for the AfterFlow knowledge health recorder.

Single-row counter; ``id=1`` is the only row that exists. The recorder is a
process-wide singleton (one process, one row) so we keep the schema trivial
and use ``INSERT ... ON CONFLICT DO UPDATE`` for atomic upserts.

Why a singleton row instead of one row per attempt?
- Health is a *running aggregate*, not a per-event log. Operators ask
  "is the knowledge layer currently healthy?" — that is a single number,
  not a list. Persisting the aggregate survives restarts so a freshly
  booted process starts with the previous health snapshot, not from zero.
- Per-attempt audit belongs in the existing run-journal / case-events
  chain, not in this health counter. Keeping the two separate keeps
  this table small enough to always fit on a single page.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class KnowledgeHealthRow(Base):
    __tablename__ = "after_sales_knowledge_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    fallback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
