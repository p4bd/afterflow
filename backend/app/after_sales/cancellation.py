"""Cancellation registry for AfterFlow runs (P0-7).

The contract:
- "Cancel" means stop spending CPU on this run. It does NOT mean roll
  back side effects that already committed.
- A pending approval that the user abandoned stays in `pending_approval`
  until it expires (24h TTL). It is NOT auto-rejected on cancel — that
  would let a flaky network double-reject a refund.
- A cancellation event is recorded so an operator can audit it.
- Cancellation races the agent loop. If the agent has already produced
  a side effect (refund reserved), the cancellation does NOT undo it.

The registry is process-local. A production deployment with multiple
Gateway processes would replace this with a Redis-backed shared store,
but the API contract stays the same.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Cancelled(Exception):
    """The run was cancelled. Carries the cancellation reason for diagnostics."""

    run_id: str
    reason: str
    cancelled_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class CancellationRegistry:
    """In-process registry; production may swap for a Redis-backed impl."""

    def __init__(self) -> None:
        self._cancelled: dict[str, tuple[str, datetime]] = {}
        self._lock = asyncio.Lock()

    async def cancel(self, *, run_id: str, reason: str) -> None:
        async with self._lock:
            self._cancelled[run_id] = (reason, datetime.now(UTC))

    async def is_cancelled(self, run_id: str) -> bool:
        async with self._lock:
            return run_id in self._cancelled

    async def reason(self, run_id: str) -> tuple[str, datetime] | None:
        async with self._lock:
            return self._cancelled.get(run_id)


async def is_cancelled(registry: CancellationRegistry, *, run_id: str) -> bool:
    return await registry.is_cancelled(run_id)


async def raise_if_cancelled(registry: CancellationRegistry, *, run_id: str) -> None:
    """Raise Cancelled if run_id is cancelled; do nothing otherwise.

    Callers should invoke this at natural await points inside a long-running
    workflow (after each tool call, before each domain step). It is a
    cooperative check: it does NOT interrupt synchronous code.
    """
    reason_ts = await registry.reason(run_id)
    if reason_ts is not None:
        reason, _ = reason_ts
        raise Cancelled(run_id=run_id, reason=reason)
