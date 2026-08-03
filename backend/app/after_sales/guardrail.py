"""Fail-closed pre-tool authorization for AfterFlow side effects."""

from datetime import UTC, datetime

from deerflow.guardrails.provider import GuardrailDecision, GuardrailReason, GuardrailRequest
from deerflow.persistence.engine import get_session_factory

from .actions import ActionStatus, payload_hash
from .repository import AfterSalesRepository


def _allow() -> GuardrailDecision:
    return GuardrailDecision(allow=True, reasons=[GuardrailReason(code="after_sales.allowed")], policy_id="after-sales-v1")


def _deny(code: str, message: str) -> GuardrailDecision:
    return GuardrailDecision(allow=False, reasons=[GuardrailReason(code=code, message=message)], policy_id="after-sales-v1")


class AfterSalesGuardrailProvider:
    name = "after-sales"
    _protected = {"create_after_sales_action", "execute_approved_action"}

    def __init__(self, repository: AfterSalesRepository | None = None) -> None:
        self._repository = repository

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        if request.tool_name not in self._protected:
            return _allow()
        return _deny("after_sales.async_required", "protected AfterFlow tools require async authorization")

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        if request.tool_name not in self._protected:
            return _allow()
        if request.is_subagent:
            return _deny("after_sales.subagent_forbidden", "subagents cannot request or execute refund actions")
        if not request.user_id:
            return _deny("after_sales.authentication_required", "authenticated user is required")
        if request.tool_name == "create_after_sales_action":
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
