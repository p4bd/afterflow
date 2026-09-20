"""Balance re-check at approval time (P0-3).

The execute-time balance check (`reserved_of`) is necessary but NOT
sufficient: between approve and execute, *other* actions on the same order
could have shifted the picture — another approval reserved the same pool,
another action was rejected and released, or the mock executor was reset.

The rule AfterFlow enforces: at the moment we approve, the requested refund
amount must still be reservable from available + reserved pool. If not,
approval fails — better to send the case back to review than to approve a
refund we already know will fail at execution.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.after_sales.actions import (
    ActionConflict,
    MockRefundExecutor,
    approve_action,
    create_refund_action,
)
from app.after_sales.balance_check import (
    BalanceCheckFailed,
    check_balance_before_approve,
)
from app.after_sales.schemas import DecisionResult, Eligibility, ResolutionAction, RiskLevel

NOW = datetime(2026, 8, 31, tzinfo=UTC)


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


def _pending():
    return create_refund_action(
        case_id="CASE-1",
        order_id="ORDER-1001",
        requested_by="agent-user",
        decision=_decision(),
        now=NOW,
    )


class TestCheckBalanceBeforeApprove:
    def test_passes_when_enough_balance(self) -> None:
        executor = MockRefundExecutor(initial_balances={"ORDER-1001": 90_900})
        check_balance_before_approve(executor, order_id="ORDER-1001", amount=90_900)  # no raise

    def test_raises_when_balance_below_amount(self) -> None:
        executor = MockRefundExecutor(initial_balances={"ORDER-1001": 90_900})
        # Pretend a previous reservation has already drained the pool.
        executor.reserve(order_id="ORDER-1001", amount=80_000)
        with pytest.raises(BalanceCheckFailed) as exc:
            check_balance_before_approve(executor, order_id="ORDER-1001", amount=90_900)
        assert exc.value.available == 10_900  # 90900 - 80000
        assert exc.value.requested == 90_900

    def test_raises_when_no_recorded_balance(self) -> None:
        executor = MockRefundExecutor()  # no balances
        with pytest.raises(BalanceCheckFailed):
            check_balance_before_approve(executor, order_id="ORDER-9999", amount=1)


class TestApproveRejectsInsufficientBalance:
    def test_approve_action_returns_action_conflict_when_balance_insufficient(self) -> None:
        # AfterFlow's safety rule: if the balance has dropped between create
        # and approve, the approval is refused with ActionConflict, not
        # silently queued for an execute-time failure.
        executor = MockRefundExecutor(initial_balances={"ORDER-1001": 90_900})
        action = _pending()
        # Drain the pool before approval.
        executor.reserve(order_id="ORDER-1001", amount=80_000)
        with pytest.raises(ActionConflict):
            approve_action(
                action,
                approver_id="supervisor-1",
                approver_roles={"admin"},
                expected_version=1,
                now=NOW,
                balance_check=lambda order_id, amount: check_balance_before_approve(executor, order_id=order_id, amount=amount),
            )
