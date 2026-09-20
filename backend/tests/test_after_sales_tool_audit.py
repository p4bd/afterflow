"""Tool-call audit tests (P0-4).

Every Tool invocation leaves a tamper-evident row that answers:
- who called the tool (actor + auth context)
- what tool, what params (hash), what decision (allow / deny)
- which reason code triggered any deny
- how long it took

The audit is the answer to the auditor's question "why was this refund
approved?" — they need to see the chain back through Tool calls to the
LLM, not just the final DecisionResult.
"""

from __future__ import annotations

import pytest

from app.after_sales.tool_audit import (
    AuditDecision,
    InMemoryToolCallAuditor,
    ToolCallAudit,
    ToolCallAuditor,
)


class TestToolCallAuditRecord:
    def test_record_carries_full_context(self) -> None:
        record = ToolCallAudit(
            case_id="CASE-1",
            actor="agent-1",
            user_role="user",
            tool_name="create_after_sales_action",
            params_hash="abc",
            decision=AuditDecision.ALLOW,
            reason_code=None,
            latency_ms=12,
            metadata={"subagent": False},
        )
        assert record.tool_name == "create_after_sales_action"
        assert record.decision is AuditDecision.ALLOW
        assert record.metadata == {"subagent": False}


class TestInMemoryToolCallAuditor:
    @pytest.mark.asyncio
    async def test_record_and_list_by_case(self) -> None:
        auditor = InMemoryToolCallAuditor()
        await auditor.record(
            case_id="CASE-1",
            actor="agent-1",
            user_role="user",
            tool_name="get_after_sales_order",
            params_hash="h1",
            decision=AuditDecision.ALLOW,
            reason_code=None,
            latency_ms=5,
        )
        await auditor.record(
            case_id="CASE-1",
            actor="agent-1",
            user_role="user",
            tool_name="create_after_sales_action",
            params_hash="h2",
            decision=AuditDecision.DENY,
            reason_code="after_sales.subagent_forbidden",
            latency_ms=2,
        )
        rows = await auditor.list_for_case("CASE-1")
        assert len(rows) == 2
        assert {r.tool_name for r in rows} == {"get_after_sales_order", "create_after_sales_action"}
        deny = next(r for r in rows if r.decision is AuditDecision.DENY)
        assert deny.reason_code == "after_sales.subagent_forbidden"

    @pytest.mark.asyncio
    async def test_list_for_unknown_case_returns_empty(self) -> None:
        auditor = InMemoryToolCallAuditor()
        assert await auditor.list_for_case("MISSING") == []


class TestToolCallAuditorProtocol:
    def test_in_memory_implements_protocol(self) -> None:
        # The repository uses this for typing; verify the contract holds.

        auditor: ToolCallAuditor = InMemoryToolCallAuditor()
        assert hasattr(auditor, "record")
        assert hasattr(auditor, "list_for_case")
