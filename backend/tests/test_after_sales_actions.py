"""Security-boundary tests for AfterFlow approval and execution state."""

from datetime import UTC, datetime, timedelta

import pytest

from app.after_sales.actions import (
    ActionConflict,
    ActionForbidden,
    ActionStatus,
    MockRefundExecutor,
    approve_action,
    create_refund_action,
    execute_approved_action,
    reject_action,
)
from app.after_sales.schemas import DecisionResult, Eligibility, ResolutionAction, RiskLevel

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def _decision(*, approval_required: bool = True, risk: RiskLevel = RiskLevel.MEDIUM) -> DecisionResult:
    return DecisionResult(
        eligibility=Eligibility.ELIGIBLE_WITH_APPROVAL if approval_required else Eligibility.ELIGIBLE,
        action=ResolutionAction.REFUND_ORIGINAL_PAYMENT,
        refund_amount=90_900,
        risk_level=risk,
        reason_code="POLICY_MATCHED",
        approval_required=approval_required,
        approval_reasons=["exceeds_operator_limit"] if approval_required else [],
        policy_refs=["AFTER-SALES-CN@2026.07"],
    )


def _pending(**kwargs):
    return create_refund_action(
        case_id="CASE-1",
        order_id="ORDER-1001",
        requested_by="agent-user",
        decision=_decision(**kwargs),
        now=NOW,
    )


def test_payload_hash_is_stable_and_tampering_is_rejected():
    action = _pending()
    approved = approve_action(action, approver_id="supervisor", approver_roles={"after_sales_supervisor"}, expected_version=1, now=NOW)

    with pytest.raises(ActionConflict, match="payload hash"):
        execute_approved_action(
            approved,
            payload={"order_id": "ORDER-1001", "amount": 1},
            expected_version=2,
            current_refundable_balance=90_900,
            executor=MockRefundExecutor(),
            now=NOW,
        )


def test_high_risk_requester_cannot_self_approve():
    action = _pending(risk=RiskLevel.HIGH)

    with pytest.raises(ActionForbidden, match="own high-risk"):
        approve_action(
            action,
            approver_id="agent-user",
            approver_roles={"after_sales_supervisor"},
            expected_version=1,
            now=NOW,
        )


def test_approval_checks_role_version_and_expiry():
    action = _pending()

    with pytest.raises(ActionForbidden, match="role"):
        approve_action(action, approver_id="staff", approver_roles={"agent"}, expected_version=1, now=NOW)
    with pytest.raises(ActionConflict, match="version"):
        approve_action(action, approver_id="supervisor", approver_roles={"after_sales_supervisor"}, expected_version=0, now=NOW)
    with pytest.raises(ActionConflict, match="expired"):
        approve_action(
            action,
            approver_id="supervisor",
            approver_roles={"after_sales_supervisor"},
            expected_version=1,
            now=NOW + timedelta(hours=25),
        )


def test_rejection_requires_comment():
    with pytest.raises(ValueError, match="comment"):
        reject_action(
            _pending(),
            approver_id="supervisor",
            approver_roles={"after_sales_supervisor"},
            expected_version=1,
            comment=" ",
            now=NOW,
        )


def test_execution_rechecks_balance_and_is_idempotent():
    action = approve_action(_pending(), approver_id="supervisor", approver_roles={"after_sales_supervisor"}, expected_version=1, now=NOW)
    executor = MockRefundExecutor()

    with pytest.raises(ActionConflict, match="balance"):
        execute_approved_action(
            action,
            payload=action.payload,
            expected_version=2,
            current_refundable_balance=1,
            executor=executor,
            now=NOW,
        )

    completed = execute_approved_action(
        action,
        payload=action.payload,
        expected_version=2,
        current_refundable_balance=90_900,
        executor=executor,
        now=NOW,
    )
    repeated = executor.refund(**action.payload, idempotency_key=action.idempotency_key)

    assert completed.status is ActionStatus.COMPLETED
    assert completed.external_transaction_id == repeated
    assert len(executor.transactions) == 1


def test_safe_within_limit_action_is_auto_approved():
    action = _pending(approval_required=False, risk=RiskLevel.LOW)

    assert action.status is ActionStatus.APPROVED
    assert action.approved_by == "system-policy"
