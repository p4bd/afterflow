"""Knowledge health tests (P0-5).

The RAG/MCP knowledge layer is the INFORM layer: if it is down, the demo
falls back to a deterministic mock so money decisions still work. But
"silent fallback" is the kind of failure mode that quietly degrades the
product: the answers get worse, no alarm fires, no metric surfaces.

These tests pin the contract: the knowledge client records every
attempt, increments a counter on fallback, and exposes a health snapshot
that an operator can read. The contract is enforced by a tiny in-memory
recorder; the SQL-backed implementation persists the same shape.
"""

from __future__ import annotations

import pytest

from app.after_sales.knowledge_health import (
    InMemoryKnowledgeHealthRecorder,
    KnowledgeHealth,
    record_attempt,
)


class TestInMemoryKnowledgeHealthRecorder:
    @pytest.mark.asyncio
    async def test_initial_health_is_healthy(self) -> None:
        recorder = InMemoryKnowledgeHealthRecorder()
        snap = await recorder.snapshot()
        assert snap.healthy is True
        assert snap.attempt_count == 0
        assert snap.fallback_count == 0

    @pytest.mark.asyncio
    async def test_success_keeps_health(self) -> None:
        recorder = InMemoryKnowledgeHealthRecorder()
        await recorder.record_success()
        await recorder.record_success()
        snap = await recorder.snapshot()
        assert snap.healthy is True
        assert snap.attempt_count == 2
        assert snap.fallback_count == 0
        assert snap.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_single_failure_stays_healthy(self) -> None:
        recorder = InMemoryKnowledgeHealthRecorder()
        await recorder.record_failure(reason="connection refused")
        snap = await recorder.snapshot()
        assert snap.healthy is True  # a single miss is not yet unhealthy
        assert snap.attempt_count == 1
        assert snap.fallback_count == 1
        assert snap.consecutive_failures == 1
        assert snap.last_error == "connection refused"

    @pytest.mark.asyncio
    async def test_consecutive_failures_mark_unhealthy(self) -> None:
        recorder = InMemoryKnowledgeHealthRecorder(unhealthy_threshold=3)
        for _ in range(3):
            await recorder.record_failure(reason="timeout")
        snap = await recorder.snapshot()
        assert snap.healthy is False
        assert snap.consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_success_resets_consecutive_failure_counter(self) -> None:
        recorder = InMemoryKnowledgeHealthRecorder(unhealthy_threshold=3)
        await recorder.record_failure(reason="x")
        await recorder.record_failure(reason="x")
        await recorder.record_success()
        snap = await recorder.snapshot()
        assert snap.healthy is True
        assert snap.consecutive_failures == 0


class TestRecordAttemptDecorator:
    @pytest.mark.asyncio
    async def test_records_success_on_normal_return(self) -> None:
        recorder = InMemoryKnowledgeHealthRecorder()
        result = await record_attempt(recorder, query="q", fn=lambda: _ok())
        assert result == "ok"
        snap = await recorder.snapshot()
        assert snap.attempt_count == 1
        assert snap.fallback_count == 0

    @pytest.mark.asyncio
    async def test_records_failure_on_exception(self) -> None:
        recorder = InMemoryKnowledgeHealthRecorder()

        async def boom() -> str:
            raise RuntimeError("MCP down")

        result = await record_attempt(recorder, query="q", fn=boom)
        assert result is None  # failure → fallback signal
        snap = await recorder.snapshot()
        assert snap.fallback_count == 1
        assert "MCP down" in (snap.last_error or "")


class TestHealthDataclass:
    def test_is_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        snap = KnowledgeHealth(
            healthy=True,
            attempt_count=0,
            fallback_count=0,
            consecutive_failures=0,
            last_error=None,
            last_attempt_at=None,
        )
        with pytest.raises(FrozenInstanceError):
            snap.healthy = False  # type: ignore[misc]


async def _ok() -> str:
    return "ok"
