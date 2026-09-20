"""Fail-closed pre-tool authorization for AfterFlow side effects."""

import time
from collections.abc import Callable
from datetime import UTC, datetime

from deerflow.guardrails.provider import GuardrailDecision, GuardrailReason, GuardrailRequest
from deerflow.persistence.engine import get_session_factory
from deerflow.runtime.user_context import get_current_user

from .actions import ActionStatus, payload_hash
from .metrics import inc_metrics
from .repository import AfterSalesRepository
from .tool_audit import AuditDecision, InMemoryToolCallAuditor, SqlToolCallAuditor, ToolCallAuditor, fingerprint_params


def _allow() -> GuardrailDecision:
    return GuardrailDecision(allow=True, reasons=[GuardrailReason(code="after_sales.allowed")], policy_id="after-sales-v1")


def _deny(code: str, message: str) -> GuardrailDecision:
    return GuardrailDecision(allow=False, reasons=[GuardrailReason(code=code, message=message)], policy_id="after-sales-v1")


class AfterSalesGuardrailProvider:
    name = "after-sales"
    _protected = {
        "create_after_sales_case",
        "update_after_sales_case",
        "create_after_sales_action",
        "execute_approved_action",
    }

    def __init__(self, repository: AfterSalesRepository | None = None) -> None:
        self._repository = repository

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        if request.tool_name not in self._protected:
            return _allow()
        if request.is_subagent:
            return _deny("after_sales.subagent_forbidden", "subagents cannot modify AfterFlow cases or actions")
        if not request.user_id and get_current_user() is None:
            return _deny("after_sales.authentication_required", "authenticated user is required")
        if request.tool_name != "execute_approved_action":
            # Case intake/update and Action request creation repeat ownership
            # checks inside the repository and do not execute a side effect.
            return _allow()
        return _deny("after_sales.async_required", "protected AfterFlow tools require async authorization")

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        if request.tool_name not in self._protected:
            return _allow()
        if request.is_subagent:
            return _deny("after_sales.subagent_forbidden", "subagents cannot request or execute refund actions")
        if not request.user_id:
            return _deny("after_sales.authentication_required", "authenticated user is required")
        if request.tool_name in {"create_after_sales_case", "update_after_sales_case", "create_after_sales_action"}:
            return _allow()
        if request.user_role != "admin":
            return _deny("after_sales.supervisor_required", "after-sales supervisor role is required")

        action_id = request.tool_input.get("action_id")
        expected_version = request.tool_input.get("expected_version")
        submitted_payload = request.tool_input.get("payload")
        if not isinstance(action_id, str) or not isinstance(expected_version, int) or not isinstance(submitted_payload, dict):
            return _deny("after_sales.invalid_request", "action_id, expected_version, and payload are required")

        repo = self._repository
        if repo is None:
            session_factory = get_session_factory()
            if session_factory is None:
                return _deny("after_sales.persistence_unavailable", "AfterFlow persistence is unavailable")
            repo = AfterSalesRepository(session_factory)
        action = await repo.get_action(action_id, user_id=None)
        if action is None:
            return _deny("after_sales.action_not_found", "action was not found")
        if action.status is not ActionStatus.APPROVED:
            return _deny("after_sales.action_not_approved", "action is not approved")
        if action.version != expected_version:
            return _deny("after_sales.version_conflict", "action version changed")
        expires_at = action.expires_at if action.expires_at.tzinfo else action.expires_at.replace(tzinfo=UTC)
        if datetime.now(UTC) >= expires_at:
            return _deny("after_sales.action_expired", "action approval expired")
        if payload_hash(action.payload) != action.payload_hash or payload_hash(submitted_payload) != action.payload_hash:
            return _deny("after_sales.payload_mismatch", "action payload does not match the approved hash")
        return _allow()


class AuditedAfterSalesGuardrailProvider:
    """Wraps AfterSalesGuardrailProvider and emits a row to the auditor.

    The wrapping is intentional: it preserves the original provider's
    decide/deny semantics while adding the audit hook. We do NOT modify the
    original provider so that callers without an auditor still get the same
    behaviour as before.

    `case_id_resolver` maps a GuardrailRequest to the case_id under which the
    audit row will be filed. Tools that aren't case-scoped (e.g. RAG query)
    should resolve to None and the audit row is still recorded but flagged.
    """

    def __init__(
        self,
        *,
        repository: AfterSalesRepository | None = None,
        auditor: ToolCallAuditor | None = None,
        case_id_resolver: Callable[[GuardrailRequest], str | None] | None = None,
        inner: AfterSalesGuardrailProvider | None = None,
    ) -> None:
        session_factory = get_session_factory()
        repository = repository or (AfterSalesRepository(session_factory) if session_factory else None)
        self._auditor = auditor or (SqlToolCallAuditor(session_factory) if session_factory else InMemoryToolCallAuditor())
        self._resolver = case_id_resolver or (lambda request: request.tool_input.get("case_id"))
        self._inner = inner or AfterSalesGuardrailProvider(repository=repository)

    @property
    def name(self) -> str:
        return f"{self._inner.name}-audited"

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        # Sync path: no audit (audit is async-only). The agent runtime should
        # always go through aevaluate.
        return self._inner.evaluate(request)

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        started = time.monotonic()
        decision = await self._inner.aevaluate(request)
        latency_ms = int((time.monotonic() - started) * 1000)
        audit_decision = AuditDecision.ALLOW if decision.allow else AuditDecision.DENY
        # Observability counter: every Guardrail decision fires this. The
        # allow/deny split drives the security team's "denied calls per
        # minute" alert; a sudden spike usually means a prompt-injection
        # attempt or a broken agent code path.
        inc_metrics("tool_audit_total", decision=audit_decision.value)
        await self._auditor.record(
            case_id=self._resolver(request) or "_unscoped",
            actor=request.user_id or "unknown",
            user_role=request.user_role or "unknown",
            tool_name=request.tool_name,
            params_hash=fingerprint_params(request.tool_input),
            decision=audit_decision,
            reason_code=(decision.reasons[0].code if decision.reasons else None),
            latency_ms=latency_ms,
            metadata={"is_subagent": request.is_subagent, "run_id": request.run_id},
        )
        return decision
