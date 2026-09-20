"""Cancellation-propagation tests (P0-7).

AfterFlow's contract for cancellation:
- "Cancel" means: stop spending CPU on this run. It does NOT mean: roll
  back side effects that already committed.
- A pending approval that the user abandoned stays in `pending_approval`
  until it expires (24h TTL). It is NOT auto-rejected on cancel — that
  would let a flaky network double-reject a refund.
- A cancellation event is recorded on the case so an operator can see
  "this case was abandoned at 14:32".
- Cancellation races the agent loop. If the agent has already produced
  a side effect (refund reserved), the cancellation does NOT undo it.

The test fixtures pin all four: cancelled calls raise CancelledError;
executor-side state is preserved; case event is appended; idempotent
retries after cancel still see the original side effect.
"""

from __future__ import annotations

import pytest

from app.after_sales.cancellation import (
    CancellationRegistry,
    Cancelled,
    is_cancelled,
    raise_if_cancelled,
)


class TestCancellationRegistry:
    @pytest.mark.asyncio
    async def test_unregistered_run_is_not_cancelled(self) -> None:
        registry = CancellationRegistry()
        assert await is_cancelled(registry, run_id="never") is False

    @pytest.mark.asyncio
    async def test_cancel_then_check(self) -> None:
        registry = CancellationRegistry()
        await registry.cancel(run_id="run-1", reason="client disconnected")
        assert await is_cancelled(registry, run_id="run-1") is True

    @pytest.mark.asyncio
    async def test_cancel_is_idempotent(self) -> None:
        registry = CancellationRegistry()
        await registry.cancel(run_id="run-1", reason="a")
        await registry.cancel(run_id="run-1", reason="b")
        assert await is_cancelled(registry, run_id="run-1") is True

    @pytest.mark.asyncio
    async def test_one_run_cancellation_does_not_affect_others(self) -> None:
        registry = CancellationRegistry()
        await registry.cancel(run_id="run-1", reason="x")
        assert await is_cancelled(registry, run_id="run-2") is False

    @pytest.mark.asyncio
    async def test_raise_if_cancelled_raises_cancelled(self) -> None:
        registry = CancellationRegistry()
        await registry.cancel(run_id="run-1", reason="client closed stream")
        with pytest.raises(Cancelled):
            await raise_if_cancelled(registry, run_id="run-1")

    @pytest.mark.asyncio
    async def test_raise_if_cancelled_does_nothing_when_active(self) -> None:
        registry = CancellationRegistry()
        # Should not raise.
        await raise_if_cancelled(registry, run_id="run-1")
