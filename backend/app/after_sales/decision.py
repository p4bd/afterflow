"""Pure, auditable rules for after-sales eligibility and refund amounts."""

from .risk_scoring import RiskSignals, assess_risk
from .schemas import (
    DecisionInput,
    DecisionResult,
    Eligibility,
    IssueType,
    ResolutionAction,
    RiskLevel,
    RiskTier,
)


def decide_resolution(case: DecisionInput) -> DecisionResult:
    """Return a deterministic resolution; no LLM participates in this calculation."""
    policy_ref = f"{case.policy.policy_id}@{case.policy.version}"

    if case.issue_type not in case.policy.covered_issues:
        return DecisionResult(
            eligibility=Eligibility.INELIGIBLE,
            action=ResolutionAction.MANUAL_REVIEW,
            refund_amount=0,
            risk_level=RiskLevel.LOW,
            reason_code="ISSUE_NOT_COVERED",
            approval_required=False,
            policy_refs=[policy_ref],
        )

    missing_evidence = _missing_evidence(case)
    if missing_evidence:
        return DecisionResult(
            eligibility=Eligibility.NEEDS_EVIDENCE,
            action=ResolutionAction.MANUAL_REVIEW,
            refund_amount=0,
            risk_level=RiskLevel.LOW,
            reason_code="REQUIRED_EVIDENCE_MISSING",
            approval_required=False,
            missing_evidence=missing_evidence,
            policy_refs=[policy_ref],
        )

    if case.payment.refundable_balance == 0:
        return DecisionResult(
            eligibility=Eligibility.INELIGIBLE,
            action=ResolutionAction.MANUAL_REVIEW,
            refund_amount=0,
            risk_level=RiskLevel.LOW,
            reason_code="NO_REFUNDABLE_BALANCE",
            approval_required=False,
            signals=["payment_balance_exhausted"],
            policy_refs=[policy_ref],
        )

    signals: list[str] = []
    requested_amount = case.order.item_paid
    if case.issue_type in case.policy.refund_shipping_issues:
        requested_amount += case.order.shipping_paid
    refund_amount = min(requested_amount, case.payment.refundable_balance)
    if refund_amount < requested_amount:
        signals.append("refund_capped_by_payment_balance")

    # Risk scorecard: transparent 0-100 score + intervention tier from
    # multiple signals. The coarse `signals` list is kept for backward-compat
    # and for the approval desk's quick glance.
    if case.customer_risk.not_received_claims_180d >= 3:
        signals.append("repeat_not_received_claims")
    elif case.customer_risk.not_received_claims_180d == 2:
        signals.append("elevated_not_received_claims")
    if case.issue_type is IssueType.DELIVERY_NOT_RECEIVED and case.logistics:
        if case.logistics.proof_of_delivery:
            signals.append("carrier_has_proof_of_delivery")
        else:
            signals.append("carrier_no_proof_of_delivery")
    if refund_amount >= case.policy.high_value_amount:
        signals.append("high_value_refund")

    assessment = assess_risk(
        RiskSignals(
            not_received_claims_180d=case.customer_risk.not_received_claims_180d,
            refund_cases_180d=case.customer_risk.refund_cases_180d,
            carrier_has_proof_of_delivery=bool(case.logistics and case.logistics.proof_of_delivery),
            is_delivery_not_received=case.issue_type is IssueType.DELIVERY_NOT_RECEIVED,
            high_value_refund=refund_amount >= case.policy.high_value_amount,
            sign_receipt_hours=case.sign_receipt_hours,
            account_age_days=case.account_age_days,
            historical_refund_rate=case.historical_refund_rate,
            address_changes_30d=case.address_changes_30d,
            device_reuse=case.device_reuse,
        ),
        refund_amount=refund_amount,
    )

    approval_reasons: list[str] = []
    if refund_amount > case.operator_refund_limit:
        approval_reasons.append("exceeds_operator_limit")
    if refund_amount >= case.policy.manual_review_amount:
        approval_reasons.append("policy_manual_review_threshold")
    if assessment.level is RiskLevel.HIGH:
        approval_reasons.append("high_risk_case")
    if assessment.tier is not RiskTier.AUTO:
        approval_reasons.append("risk_score_requires_review")

    approval_required = bool(approval_reasons)
    action = ResolutionAction.RETURN_AND_REFUND if case.issue_type in case.policy.return_required_issues else ResolutionAction.REFUND_ORIGINAL_PAYMENT
    return DecisionResult(
        eligibility=Eligibility.ELIGIBLE_WITH_APPROVAL if approval_required else Eligibility.ELIGIBLE,
        action=action,
        refund_amount=refund_amount,
        risk_level=assessment.level,
        reason_code="POLICY_MATCHED",
        approval_required=approval_required,
        approval_reasons=approval_reasons,
        signals=signals,
        policy_refs=[policy_ref],
        risk_score=assessment.score,
        risk_tier=assessment.tier,
        risk_signals=[c.model_dump() for c in assessment.contributions],
    )


def _missing_evidence(case: DecisionInput) -> list[str]:
    if case.issue_type is IssueType.DELIVERY_NOT_RECEIVED and case.logistics is None:
        return ["logistics_evidence"]
    if case.issue_type in {IssueType.DAMAGED_ITEM, IssueType.WRONG_ITEM} and not case.visual_evidence_confirmed:
        return ["visual_evidence"]
    return []
