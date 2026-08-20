"""Tests for deterministic after-sales resolution decisions."""

import pytest
from pydantic import ValidationError

from app.after_sales.decision import decide_resolution
from app.after_sales.schemas import (
    CustomerRiskContext,
    DecisionInput,
    Eligibility,
    IssueType,
    LogisticsEvidence,
    OrderContext,
    PaymentContext,
    PolicySnapshot,
    ResolutionAction,
    RiskLevel,
)


def _case(**overrides) -> DecisionInput:
    data = {
        "issue_type": IssueType.DELIVERY_NOT_RECEIVED,
        "order": OrderContext(order_id="ORDER-1001", item_paid=89_900, shipping_paid=1_000),
        "payment": PaymentContext(refundable_balance=90_900),
        "logistics": LogisticsEvidence(status="in_transit", proof_of_delivery=False),
        "customer_risk": CustomerRiskContext(not_received_claims_180d=0, refund_cases_180d=0),
        "policy": PolicySnapshot(
            policy_id="AFTER-SALES-CN",
            version="2026.07",
            covered_issues=set(IssueType),
            refund_shipping_issues={IssueType.DELIVERY_NOT_RECEIVED},
            return_required_issues={IssueType.DAMAGED_ITEM, IssueType.WRONG_ITEM},
            manual_review_amount=100_000,
            high_value_amount=50_000,
        ),
        "operator_refund_limit": 20_000,
        "visual_evidence_confirmed": False,
    }
    data.update(overrides)
    return DecisionInput(**data)


def test_delivery_not_received_without_pod_recommends_refund_and_approval():
    result = decide_resolution(_case())

    assert result.eligibility is Eligibility.ELIGIBLE_WITH_APPROVAL
    assert result.action is ResolutionAction.REFUND_ORIGINAL_PAYMENT
    assert result.refund_amount == 90_900
    assert result.risk_level is RiskLevel.MEDIUM
    assert "carrier_no_proof_of_delivery" in result.signals
    assert "exceeds_operator_limit" in result.approval_reasons
    assert result.policy_refs == ["AFTER-SALES-CN@2026.07"]


def test_refund_is_capped_by_remaining_payment_balance():
    result = decide_resolution(
        _case(
            issue_type=IssueType.REFUND_AMOUNT_DISPUTE,
            payment=PaymentContext(refundable_balance=12_300),
            operator_refund_limit=20_000,
        )
    )

    assert result.refund_amount == 12_300
    assert result.eligibility is Eligibility.ELIGIBLE
    assert "refund_capped_by_payment_balance" in result.signals


def test_uncovered_issue_is_ineligible():
    policy = _case().policy.model_copy(update={"covered_issues": {IssueType.DAMAGED_ITEM}})

    result = decide_resolution(_case(policy=policy))

    assert result.eligibility is Eligibility.INELIGIBLE
    assert result.action is ResolutionAction.MANUAL_REVIEW
    assert result.refund_amount == 0
    assert result.reason_code == "ISSUE_NOT_COVERED"


def test_missing_required_logistics_evidence_requests_evidence():
    result = decide_resolution(_case(logistics=None))

    assert result.eligibility is Eligibility.NEEDS_EVIDENCE
    assert result.refund_amount == 0
    assert result.missing_evidence == ["logistics_evidence"]


def test_repeat_not_received_claims_force_high_risk_review():
    result = decide_resolution(
        _case(
            order=OrderContext(order_id="ORDER-1002", item_paid=9_900, shipping_paid=0),
            payment=PaymentContext(refundable_balance=9_900),
            customer_risk=CustomerRiskContext(not_received_claims_180d=3, refund_cases_180d=4),
        )
    )

    assert result.risk_level is RiskLevel.HIGH
    assert result.eligibility is Eligibility.ELIGIBLE_WITH_APPROVAL
    assert "repeat_not_received_claims" in result.signals
    assert "high_risk_case" in result.approval_reasons


def test_money_fields_reject_negative_values():
    with pytest.raises(ValidationError):
        OrderContext(order_id="ORDER-INVALID", item_paid=-1, shipping_paid=0)


def test_risk_scorecard_output_is_explainable():
    result = decide_resolution(
        _case(
            customer_risk=CustomerRiskContext(not_received_claims_180d=3, refund_cases_180d=4),
        )
    )

    assert result.risk_score >= 40
    assert result.risk_tier == "supervisor"
    assert result.risk_level is RiskLevel.HIGH
    assert any(s["signal"] == "repeat_not_received_claims" for s in result.risk_signals)


def test_sign_receipt_hours_feeds_the_scorecard():
    result = decide_resolution(
        _case(
            issue_type=IssueType.DAMAGED_ITEM,
            visual_evidence_confirmed=True,
            sign_receipt_hours=1,
        )
    )

    assert any(s["signal"] == "immediate_claim_after_receipt" for s in result.risk_signals)
    assert "high_value_refund" in result.signals
