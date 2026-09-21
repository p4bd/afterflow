"""ORM row for the AfterFlow tool-call audit log.

One row per tool invocation (allowed or denied). Indexed by case_id so an
auditor can pull the full call chain for one case in O(rows-for-case).
The hash chain on `case_events` and this table together give the auditor
"what the agent did + what happened to the case".
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ToolCallAuditRow(Base):
    __tablename__ = "after_sales_tool_audit"
    __table_args__ = (Index("ix_tool_audit_case_recorded", "case_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    user_role: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
