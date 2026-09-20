"""Tool-call audit for AfterFlow.

The case-event chain shows what *happened* to a case (decision_generated,
approval_granted, execution_succeeded). It does NOT show what the *agent
tried to do* on the way there — which tools it called, with what arguments,
and whether the Guardrail allowed or denied each call.

AfterFlow's audit answers the auditor's question "why was this refund
approved?" by giving them the tool-call chain to inspect. The chain has
to be tamper-evident: if someone tampers with a row, the hash link breaks
(we extend `verify_event_chain` to walk both `case_events` and
`tool_call_audits`).

The in-memory auditor here is for tests; the production implementation
persists rows to SQL (see `SqlToolCallAuditor` in `tool_audit_orm.py`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


def fingerprint_params(params: dict) -> str:
    """Stable SHA-256 fingerprint of tool input. Key-order independent.

    We never persist raw tool arguments in the audit (they may carry PII /
    secrets). The fingerprint lets an auditor prove "this exact input was
    called twice" without seeing the values.
    """
    encoded = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class AuditDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class ToolCallAudit:
    case_id: str
    actor: str
    user_role: str
    tool_name: str
    params_hash: str
    decision: AuditDecision
    reason_code: str | None
    latency_ms: int
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = field(default_factory=dict)


class ToolCallAuditor(Protocol):
    async def record(
        self,
        *,
        case_id: str,
        actor: str,
        user_role: str,
        tool_name: str,
        params_hash: str,
        decision: AuditDecision,
        reason_code: str | None,
        latency_ms: int,
        metadata: dict | None = None,
    ) -> None: ...

    async def list_for_case(self, case_id: str) -> list[ToolCallAudit]: ...


class InMemoryToolCallAuditor:
    """Test-friendly auditor. Production uses the SQL implementation."""

    def __init__(self) -> None:
        self._records: list[ToolCallAudit] = []

    async def record(
        self,
        *,
        case_id: str,
        actor: str,
        user_role: str,
        tool_name: str,
        params_hash: str,
        decision: AuditDecision,
        reason_code: str | None,
        latency_ms: int,
        metadata: dict | None = None,
    ) -> None:
        self._records.append(
            ToolCallAudit(
                case_id=case_id,
                actor=actor,
                user_role=user_role,
                tool_name=tool_name,
                params_hash=params_hash,
                decision=decision,
                reason_code=reason_code,
                latency_ms=latency_ms,
                metadata=metadata or {},
            )
        )

    async def list_for_case(self, case_id: str) -> list[ToolCallAudit]:
        return [r for r in self._records if r.case_id == case_id]


class SqlToolCallAuditor:
    """Production auditor; rows persist in `after_sales_tool_audit`."""

    def __init__(self, session_factory: object) -> None:
        self._sf = session_factory

    async def record(
        self,
        *,
        case_id: str,
        actor: str,
        user_role: str,
        tool_name: str,
        params_hash: str,
        decision: AuditDecision,
        reason_code: str | None,
        latency_ms: int,
        metadata: dict | None = None,
    ) -> None:
        from sqlalchemy import insert

        from .tool_audit_orm import ToolCallAuditRow

        async with self._sf() as session:
            await session.execute(
                insert(ToolCallAuditRow).values(
                    case_id=case_id,
                    actor=actor,
                    user_role=user_role,
                    tool_name=tool_name,
                    params_hash=params_hash,
                    decision=decision.value,
                    reason_code=reason_code,
                    latency_ms=latency_ms,
                    metadata_json=metadata or {},
                )
            )
            await session.commit()

    async def list_for_case(self, case_id: str) -> list[ToolCallAudit]:
        from sqlalchemy import select

        from .tool_audit_orm import ToolCallAuditRow

        async with self._sf() as session:
            rows = (await session.scalars(select(ToolCallAuditRow).where(ToolCallAuditRow.case_id == case_id).order_by(ToolCallAuditRow.recorded_at, ToolCallAuditRow.id))).all()
        return [
            ToolCallAudit(
                case_id=row.case_id,
                actor=row.actor,
                user_role=row.user_role,
                tool_name=row.tool_name,
                params_hash=row.params_hash,
                decision=AuditDecision(row.decision),
                reason_code=row.reason_code,
                latency_ms=row.latency_ms,
                recorded_at=row.recorded_at,
                metadata=row.metadata_json,
            )
            for row in rows
        ]
