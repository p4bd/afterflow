"""Knowledge-layer health for AfterFlow (P0-5).

The RAG/MCP knowledge layer is the INFORM layer. It can fail open to a
deterministic mock and money decisions still work — but "silent fallback"
is the failure mode where the product quietly degrades without anybody
noticing. The fix is to make the failure visible.

`KnowledgeHealthRecorder` records every attempt and exposes a snapshot:
- attempt_count / fallback_count
- consecutive_failures (resets on success)
- last_error / last_attempt_at
- a `healthy` flag, computed against `unhealthy_threshold` (default 3)

A `record_attempt` decorator wraps any async knowledge fetcher: success
records as success; any exception records as failure and is swallowed so
the caller can fall back to the mock. This keeps the existing fail-open
behaviour of `retrieve_knowledge` but now the operator can SEE when it
happens.

The SQL-backed implementation persists the same data; this module only
defines the contract and the in-memory implementation that production
code uses to introspect / health-check.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class KnowledgeHealth:
    healthy: bool
    attempt_count: int
    fallback_count: int
    consecutive_failures: int
    last_error: str | None
    last_attempt_at: datetime | None


class KnowledgeHealthRecorder(Protocol):
    async def record_success(self) -> None: ...

    async def record_failure(self, *, reason: str) -> None: ...

    async def snapshot(self) -> KnowledgeHealth: ...


class InMemoryKnowledgeHealthRecorder:
    """In-process recorder. Production uses the SQL-backed variant."""

    def __init__(self, *, unhealthy_threshold: int = 3) -> None:
        self._threshold = unhealthy_threshold
        self._attempt_count = 0
        self._fallback_count = 0
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._last_attempt_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def record_success(self) -> None:
        async with self._lock:
            self._attempt_count += 1
            self._consecutive_failures = 0
            self._last_attempt_at = datetime.now(UTC)

    async def record_failure(self, *, reason: str) -> None:
        async with self._lock:
            self._attempt_count += 1
            self._fallback_count += 1
            self._consecutive_failures += 1
            self._last_error = reason
            self._last_attempt_at = datetime.now(UTC)

    async def snapshot(self) -> KnowledgeHealth:
        async with self._lock:
            return KnowledgeHealth(
                healthy=self._consecutive_failures < self._threshold,
                attempt_count=self._attempt_count,
                fallback_count=self._fallback_count,
                consecutive_failures=self._consecutive_failures,
                last_error=self._last_error,
                last_attempt_at=self._last_attempt_at,
            )


class SqlKnowledgeHealthRecorder:
    """Production SQL-backed health recorder.

    A single row (id=1) holds the rolling aggregate counters; upserts are
    atomic. Survives process restart so a freshly booted Gateway starts
    with the previous health snapshot instead of zero — that matters for
    the "silent fallback" detection: an unhealthy count of 5 from before
    the restart shouldn't immediately reset to 0 on the next failure.

    Implementation choice: ``INSERT ... ON CONFLICT DO UPDATE`` (Postgres /
    SQLite-supported dialect ``insert`` + ``on_conflict_do_update``).
    Cheaper than a SELECT-then-INSERT-then-UPDATE dance, and atomic on
    both backends (sqlite since 3.24, postgres since 9.5).
    """

    def __init__(self, session_factory: object, *, unhealthy_threshold: int = 3) -> None:
        self._sf = session_factory
        self._threshold = unhealthy_threshold

    async def record_success(self) -> None:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from .knowledge_health_orm import KnowledgeHealthRow

        now = datetime.now(UTC)
        async with self._sf() as session:
            # Atomic upsert against the singleton id=1 row. The INSERT path
            # seeds the row on first use; the ON CONFLICT branch nudges
            # the counters on every subsequent call without a SELECT round
            # trip.
            stmt = sqlite_insert(KnowledgeHealthRow).values(
                id=1,
                attempt_count=1,
                fallback_count=0,
                consecutive_failures=0,
                last_error=None,
                last_attempt_at=now,
                updated_at=now,
            )
            upsert = stmt.on_conflict_do_update(
                index_elements=[KnowledgeHealthRow.id],
                set_={
                    "attempt_count": KnowledgeHealthRow.attempt_count + 1,
                    "consecutive_failures": 0,
                    "last_attempt_at": now,
                    "updated_at": now,
                },
            )
            await session.execute(upsert)
            await session.commit()

    async def record_failure(self, *, reason: str) -> None:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from .knowledge_health_orm import KnowledgeHealthRow

        now = datetime.now(UTC)
        async with self._sf() as session:
            stmt = sqlite_insert(KnowledgeHealthRow).values(
                id=1,
                attempt_count=1,
                fallback_count=1,
                consecutive_failures=1,
                last_error=reason[:512],
                last_attempt_at=now,
                updated_at=now,
            )
            upsert = stmt.on_conflict_do_update(
                index_elements=[KnowledgeHealthRow.id],
                set_={
                    "attempt_count": KnowledgeHealthRow.attempt_count + 1,
                    "fallback_count": KnowledgeHealthRow.fallback_count + 1,
                    "consecutive_failures": KnowledgeHealthRow.consecutive_failures + 1,
                    "last_error": reason[:512],
                    "last_attempt_at": now,
                    "updated_at": now,
                },
            )
            await session.execute(upsert)
            await session.commit()

    async def snapshot(self) -> KnowledgeHealth:
        from sqlalchemy import select

        from .knowledge_health_orm import KnowledgeHealthRow

        async with self._sf() as session:
            row = await session.scalar(select(KnowledgeHealthRow).where(KnowledgeHealthRow.id == 1))
            if row is None:
                # Defensive: a recorder that hasn't recorded anything yet
                # could be query'd before its first write. This matches the
                # in-memory recorder's initial state.
                return KnowledgeHealth(
                    healthy=True,
                    attempt_count=0,
                    fallback_count=0,
                    consecutive_failures=0,
                    last_error=None,
                    last_attempt_at=None,
                )
            return KnowledgeHealth(
                healthy=row.consecutive_failures < self._threshold,
                attempt_count=row.attempt_count,
                fallback_count=row.fallback_count,
                consecutive_failures=row.consecutive_failures,
                last_error=row.last_error,
                last_attempt_at=row.last_attempt_at,
            )


async def record_attempt(
    recorder: KnowledgeHealthRecorder,
    *,
    query: str,
    fn: Callable[[], Awaitable[Any]],
) -> Any | None:
    """Run fn(); record success or failure; on failure return None (fallback signal).

    The caller is expected to substitute a deterministic mock when the
    return is None. This keeps the fail-open semantics of
    `retrieve_knowledge` while making the failure visible to the operator.
    """
    try:
        result = await fn()
        await recorder.record_success()
        return result
    except Exception as exc:  # noqa: BLE001 — we want to swallow here, but record
        await recorder.record_failure(reason=f"{type(exc).__name__}: {exc}")
        return None
