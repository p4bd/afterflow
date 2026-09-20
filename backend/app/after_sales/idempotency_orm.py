"""ORM row for the AfterFlow idempotency cache.

Single table; the unique index on (key, endpoint) is the canonical race
resolution — two concurrent writers with the same key will both try to insert;
only one INSERT succeeds, the other catches IntegrityError and re-reads the
already-existing record.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class IdempotencyRecordRow(Base):
    __tablename__ = "after_sales_idempotency"
    __table_args__ = (
        UniqueConstraint("key", "endpoint", name="uq_idempotency_key_endpoint"),
        Index("ix_idempotency_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
