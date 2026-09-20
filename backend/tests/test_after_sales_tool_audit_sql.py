"""SQL-backed tool-call audit tests."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.after_sales.tool_audit import AuditDecision, SqlToolCallAuditor
from deerflow.persistence.base import Base


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    from app.after_sales import tool_audit_orm  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_sql_auditor_record_and_list(session_factory) -> None:
    auditor = SqlToolCallAuditor(session_factory)
    await auditor.record(
        case_id="CASE-1",
        actor="agent-1",
        user_role="user",
        tool_name="create_after_sales_action",
        params_hash="h1",
        decision=AuditDecision.DENY,
        reason_code="after_sales.subagent_forbidden",
        latency_ms=3,
    )
    rows = await auditor.list_for_case("CASE-1")
    assert len(rows) == 1
    assert rows[0].decision is AuditDecision.DENY
    assert rows[0].reason_code == "after_sales.subagent_forbidden"


@pytest.mark.asyncio
async def test_sql_auditor_filters_by_case(session_factory) -> None:
    auditor = SqlToolCallAuditor(session_factory)
    await auditor.record(case_id="CASE-1", actor="a", user_role="user", tool_name="t", params_hash="p", decision=AuditDecision.ALLOW, reason_code=None, latency_ms=1)
    await auditor.record(case_id="CASE-2", actor="b", user_role="admin", tool_name="t", params_hash="p", decision=AuditDecision.ALLOW, reason_code=None, latency_ms=1)
    rows = await auditor.list_for_case("CASE-1")
    assert len(rows) == 1
    assert rows[0].actor == "a"
