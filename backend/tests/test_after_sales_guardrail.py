"""Tests for the AfterFlow pre-tool-call authorization provider."""

from datetime import UTC, datetime, timedelta

import pytest

from app.after_sales.actions import ActionRequest, ActionStatus, payload_hash
from app.after_sales.guardrail import AfterSalesGuardrailProvider
from deerflow.guardrails.provider import GuardrailRequest


class FakeRepository:
    def __init__(self, action):
        self.action = action

    async def get_action(self, action_id, *, user_id):
        return self.action if self.action and self.action.id == action_id and user_id is None else None


def _action(**overrides):
    data = {
        "id": "ACTION-1",
        "case_id": "CASE-1",
        "payload": {"order_id": "ORDER-1001", "amount": 1000},
        "payload_hash": payload_hash({"order_id": "ORDER-1001", "amount": 1000}),
        "status": ActionStatus.APPROVED,
        "risk_level": "medium",
        "requested_by": "agent-1",
        "idempotency_key": "idem-1",
        "approved_by": "admin-1",
        "approved_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        "version": 2,
    }
    data.update(overrides)
    return ActionRequest(**data)


@pytest.mark.asyncio
async def test_read_tools_are_allowed_without_database_lookup():
    provider = AfterSalesGuardrailProvider(repository=FakeRepository(None))
    decision = await provider.aevaluate(GuardrailRequest(tool_name="get_after_sales_order", tool_input={}))

    assert decision.allow is True


@pytest.mark.asyncio
async def test_execute_requires_admin_approved_matching_action():
    provider = AfterSalesGuardrailProvider(repository=FakeRepository(_action()))
    request = GuardrailRequest(
        tool_name="execute_approved_action",
        tool_input={
            "action_id": "ACTION-1",
            "expected_version": 2,
            "payload": {"order_id": "ORDER-1001", "amount": 1000},
        },
        user_id="admin-1",
        user_role="admin",
    )

    assert (await provider.aevaluate(request)).allow is True
    request.user_role = "user"
    denied = await provider.aevaluate(request)
    assert denied.allow is False
    assert denied.reasons[0].code == "after_sales.supervisor_required"


@pytest.mark.asyncio
async def test_subagents_and_stale_versions_are_denied():
    provider = AfterSalesGuardrailProvider(repository=FakeRepository(_action()))
    request = GuardrailRequest(
        tool_name="execute_approved_action",
        tool_input={
            "action_id": "ACTION-1",
            "expected_version": 1,
            "payload": {"order_id": "ORDER-1001", "amount": 1000},
        },
        user_id="admin-1",
        user_role="admin",
        is_subagent=True,
    )

    assert (await provider.aevaluate(request)).reasons[0].code == "after_sales.subagent_forbidden"
    request.is_subagent = False
    assert (await provider.aevaluate(request)).reasons[0].code == "after_sales.version_conflict"
