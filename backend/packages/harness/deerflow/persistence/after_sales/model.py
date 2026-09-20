from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ServiceCaseRow(Base):
    __tablename__ = "service_cases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    issue_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="decided", index=True)
    complaint_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    customer_expectation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    next_step: Mapped[str] = mapped_column(String(64), nullable=False, default="review_decision")
    reply_draft: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    decision_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class ActionRequestRow(Base):
    __tablename__ = "action_requests"

    # DB-level guarantee that a case has at most one active (pending/approved)
    # action at a time — the app-layer check in create_action is a friendly
    # error, this index is the authoritative backstop against TOCTOU races.
    __table_args__ = (
        Index(
            "uq_action_one_active_per_case",
            "case_id",
            unique=True,
            sqlite_where=text("status IN ('pending_approval', 'approved')"),
            postgresql_where=text("status IN ('pending_approval', 'approved')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("service_cases.id"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    required_role: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    external_transaction_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approvers_required: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approver_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reserved: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)


class CaseEventRow(Base):
    __tablename__ = "case_events"

    # DB-level invariant (P0-B): within a case, seq is monotonic and unique.
    # Without this, two concurrent ``append_event`` calls can each compute
    # the same ``last.seq + 1`` and both INSERT, forking the audit chain.
    # The application layer (``AfterSalesRepository.append_event``) catches
    # ``IntegrityError`` and re-reads the last seq with a bounded retry; the
    # constraint is the authoritative race resolution that the retry
    # ultimately depends on. Per-case uniqueness replaces the pre-existing
    # global ``UNIQUE(seq)`` invariant: the application code computes
    # ``last.seq + 1`` per case, so a global unique seq would forbid two
    # cases from ever both having an event with seq=1 — an artifact of the
    # original autoincrement design that didn't survive contact with the
    # per-case append pattern. The corresponding ``ix_case_events_seq``
    # unique INDEX added in 0009 is dropped by migration 0011.
    __table_args__ = (UniqueConstraint("case_id", "seq", name="uq_case_events_case_id_seq"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("service_cases.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    # Per-case monotonic sequence: orders the chain deterministically,
    # independent of uuid4/created_at ties when events land in the same
    # microsecond. Per-case uniqueness is enforced by
    # ``uq_case_events_case_id_seq`` above (P0-B). The column-level
    # ``unique=True`` from the pre-P0-B model is removed: per-case
    # increment means two cases naturally both start at seq=1, and a
    # global UNIQUE(seq) would forbid that.
    seq: Mapped[int] = mapped_column(Integer, primary_key=False, autoincrement=True)
