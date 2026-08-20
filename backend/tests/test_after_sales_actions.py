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
    approved = approve_action(action, approver_id="supervisor", approver_roles={"admin"}, expected_version=1, now=NOW)

    with pytest.raises(ActionConflict, match="payload hash"):
        execute_approved_action(
            approved,
            payload={"order_id": "ORDER-1001", "amount": 1},
            expected_version=2,
            current_refundable_balance=90_900,
            executor=MockRefundExecutor(),
            now=NOW,
        )


def test_requester_cannot_self_approve_any_risk_level():
    for risk in (RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW):
        action = _pending(risk=risk)

        with pytest.raises(ActionForbidden, match="own action"):
            approve_action(
                action,
                approver_id="agent-user",
                approver_roles={"admin"},
                expected_version=1,
                now=NOW,
            )


def test_approval_uses_real_role_not_ghost_role():
    # required_role is now "admin" (the real auth role); approving with a
    # fabricated "after_sales_supervisor" role must fail.
    action = _pending()

    with pytest.raises(ActionForbidden, match="required role"):
        approve_action(
            action,
            approver_id="supervisor",
            approver_roles={"after_sales_supervisor"},
            expected_version=1,
            now=NOW,
        )


def test_approval_checks_role_version_and_expiry():
    action = _pending()

    with pytest.raises(ActionForbidden, match="role"):
        approve_action(action, approver_id="staff", approver_roles={"agent"}, expected_version=1, now=NOW)
    with pytest.raises(ActionConflict, match="version"):
        approve_action(action, approver_id="supervisor", approver_roles={"admin"}, expected_version=0, now=NOW)
    with pytest.raises(ActionConflict, match="expired"):
        approve_action(
            action,
            approver_id="supervisor",
            approver_roles={"admin"},
            expected_version=1,
            now=NOW + timedelta(hours=25),
        )


def test_rejection_requires_comment():
    with pytest.raises(ValueError, match="comment"):
        reject_action(
            _pending(),
            approver_id="supervisor",
            approver_roles={"admin"},
            expected_version=1,
            comment=" ",
            now=NOW,
        )


def test_execution_rechecks_reserved_balance_and_is_idempotent():
    action = approve_action(_pending(), approver_id="supervisor", approver_roles={"admin"}, expected_version=1, now=NOW)
    executor = MockRefundExecutor(initial_balances={"ORDER-1001": 90_900})

    with pytest.raises(ActionConflict, match="balance"):
        execute_approved_action(
            action,
            payload=action.payload,
            expected_version=2,
            current_refundable_balance=1,
            executor=executor,
            now=NOW,
        )

    # approval freezes the funds; execution captures from the reservation
    assert executor.reserve(order_id="ORDER-1001", amount=90_900) is True
    assert executor.reserved_of("ORDER-1001") == 90_900

    completed = execute_approved_action(
        action,
        payload=action.payload,
        expected_version=2,
        current_refundable_balance=executor.reserved_of("ORDER-1001"),
        executor=executor,
        now=NOW,
    )
    repeated = executor.refund(**action.payload, idempotency_key=action.idempotency_key)

    assert completed.status is ActionStatus.COMPLETED
    assert completed.external_transaction_id == repeated
    assert len(executor.transactions) == 1
    assert executor.reserved_of("ORDER-1001") == 0


def test_authorize_capture_prevents_double_spend():
    executor = MockRefundExecutor(initial_balances={"ORDER-1001": 90_900})

    # First approval freezes the full amount; nothing remains available.
    assert executor.reserve(order_id="ORDER-1001", amount=90_900) is True
    assert executor.balance_of("ORDER-1001") == 0
    assert executor.reserved_of("ORDER-1001") == 90_900

    # A second refund on the same order cannot be funded (double-spend blocked).
    assert executor.reserve(order_id="ORDER-1001", amount=1) is False

    # Rejecting/expiry releases the reservation back to available.
    executor.release(order_id="ORDER-1001", amount=90_900)
    assert executor.balance_of("ORDER-1001") == 90_900
    assert executor.reserved_of("ORDER-1001") == 0


def test_safe_within_limit_action_is_auto_approved():
    action = _pending(approval_required=False, risk=RiskLevel.LOW)

    assert action.status is ActionStatus.APPROVED
    assert action.approved_by == "system-policy"


def test_idempotency_key_is_deterministic_business_key():
    a1 = _pending()
    a2 = _pending()

    # same case + action type + payload => same business idempotency key
    assert a1.idempotency_key == a2.idempotency_key
    # different case => different key
    other = create_refund_action(
        case_id="CASE-OTHER",
        order_id="ORDER-1001",
        requested_by="agent-user",
        decision=_decision(),
        now=NOW,
    )
    assert other.idempotency_key != a1.idempotency_key
    # different amount => different key
    changed = create_refund_action(
        case_id="CASE-1",
        order_id="ORDER-1001",
        requested_by="agent-user",
        decision=_decision().model_copy(update={"refund_amount": 1}),
        now=NOW,
    )
    assert changed.idempotency_key != a1.idempotency_key


def test_execution_consumes_the_reservation_not_the_available_pool():
    action = approve_action(_pending(), approver_id="supervisor", approver_roles={"admin"}, expected_version=1, now=NOW)
    executor = MockRefundExecutor(initial_balances={"ORDER-1001": 90_900})

    # The amount must be frozen at approval time before execution can capture it.
    assert executor.reserve(order_id="ORDER-1001", amount=90_900) is True
    completed = execute_approved_action(
        action,
        payload=action.payload,
        expected_version=2,
        current_refundable_balance=executor.reserved_of("ORDER-1001"),
        executor=executor,
        now=NOW,
    )

    assert completed.status is ActionStatus.COMPLETED
    assert executor.reserved_of("ORDER-1001") == 0
    # Available pool stays reserved-accounted; it never returned to spendable.
    assert executor.balance_of("ORDER-1001") == 0


def test_four_eyes_action_requires_two_distinct_approvers():
    decision = DecisionResult(
        eligibility=Eligibility.ELIGIBLE_WITH_APPROVAL,
        action=ResolutionAction.REFUND_ORIGINAL_PAYMENT,
        refund_amount=500_001,
        risk_level=RiskLevel.HIGH,
        risk_tier="four_eyes",
        reason_code="POLICY_MATCHED",
        approval_required=True,
        approval_reasons=["high_risk_case", "risk_score_requires_review"],
        policy_refs=["AFTER-SALES-CN@2026.07"],
    )
    action = create_refund_action(
        case_id="CASE-FOUR-EYES",
        order_id="ORDER-1001",
        requested_by="requester-1",
        decision=decision,
        now=NOW,
    )

    assert action.approvers_required == 2
    assert action.status is ActionStatus.PENDING_APPROVAL

    # First approver signs -> still pending (needs a second, distinct signer).
    once = approve_action(
        action,
        approver_id="approver-a",
        approver_roles={"admin"},
        expected_version=1,
        now=NOW,
    )
    assert once.status is ActionStatus.PENDING_APPROVAL
    assert once.approver_ids == ["approver-a"]

    # The same approver cannot sign twice.
    with pytest.raises(ActionForbidden, match="already approved"):
        approve_action(
            once,
            approver_id="approver-a",
            approver_roles={"admin"},
            expected_version=2,
            now=NOW,
        )

    # A second, distinct approver completes the four-eyes sign-off.
    approved = approve_action(
        once,
        approver_id="approver-b",
        approver_roles={"admin"},
        expected_version=2,
        now=NOW,
    )
    assert approved.status is ActionStatus.APPROVED
    assert approved.approver_ids == ["approver-a", "approver-b"]


def test_single_approval_action_approves_immediately():
    action = _pending()

    assert action.approvers_required == 1
    approved = approve_action(
        action,
        approver_id="supervisor",
        approver_roles={"admin"},
        expected_version=1,
        now=NOW,
    )
    assert approved.status is ActionStatus.APPROVED


def test_return_and_refund_decision_generates_executable_action():
    # Damaged/wrong-item decisions produce RETURN_AND_REFUND with a computed
    # refund_amount; that amount must be executable as a refund Action, not
    # rejected by the boundary (regression for the broken C3 path).
    decision = DecisionResult(
        eligibility=Eligibility.ELIGIBLE_WITH_APPROVAL,
        action=ResolutionAction.RETURN_AND_REFUND,
        refund_amount=31_600,
        risk_level=RiskLevel.MEDIUM,
        reason_code="POLICY_MATCHED",
        approval_required=True,
        approval_reasons=["exceeds_operator_limit"],
        policy_refs=["AFTER-SALES-CN@2026.07"],
    )

    action = create_refund_action(
        case_id="CASE-RETURN",
        order_id="ORDER-1003",
        requested_by="agent-user",
        decision=decision,
        now=NOW,
    )

    assert action.payload == {"order_id": "ORDER-1003", "amount": 31_600}
    assert action.status is ActionStatus.PENDING_APPROVAL
