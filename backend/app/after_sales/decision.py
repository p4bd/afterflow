"""Pure, auditable rules for after-sales eligibility and refund amounts."""

from .schemas import (
    DecisionInput,
    DecisionResult,
    Eligibility,
    IssueType,
    ResolutionAction,
    RiskLevel,
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

    risk_level = _assess_risk(case, refund_amount, signals)
    approval_reasons: list[str] = []
    if refund_amount > case.operator_refund_limit:
        approval_reasons.append("exceeds_operator_limit")
    if refund_amount >= case.policy.manual_review_amount:
        approval_reasons.append("policy_manual_review_threshold")
    if risk_level is RiskLevel.HIGH:
        approval_reasons.append("high_risk_case")

    approval_required = bool(approval_reasons)
    action = ResolutionAction.RETURN_AND_REFUND if case.issue_type in case.policy.return_required_issues else ResolutionAction.REFUND_ORIGINAL_PAYMENT
    return DecisionResult(
        eligibility=Eligibility.ELIGIBLE_WITH_APPROVAL if approval_required else Eligibility.ELIGIBLE,
        action=action,
        refund_amount=refund_amount,
        risk_level=risk_level,
        reason_code="POLICY_MATCHED",
        approval_required=approval_required,
        approval_reasons=approval_reasons,
        signals=signals,
        policy_refs=[policy_ref],
    )


def _missing_evidence(case: DecisionInput) -> list[str]:
    if case.issue_type is IssueType.DELIVERY_NOT_RECEIVED and case.logistics is None:
        return ["logistics_evidence"]
    if case.issue_type in {IssueType.DAMAGED_ITEM, IssueType.WRONG_ITEM} and not case.visual_evidence_confirmed:
        return ["visual_evidence"]
    return []


def _assess_risk(case: DecisionInput, refund_amount: int, signals: list[str]) -> RiskLevel:
    high_risk = False
    medium_risk = False

    if case.customer_risk.not_received_claims_180d >= 3:
        signals.append("repeat_not_received_claims")
        high_risk = True
    elif case.customer_risk.not_received_claims_180d == 2:
        signals.append("elevated_not_received_claims")
        medium_risk = True

    if case.issue_type is IssueType.DELIVERY_NOT_RECEIVED and case.logistics:
        if case.logistics.proof_of_delivery:
            signals.append("carrier_has_proof_of_delivery")
            high_risk = True
        else:
            signals.append("carrier_no_proof_of_delivery")

    if refund_amount >= case.policy.high_value_amount:
        signals.append("high_value_refund")
        medium_risk = True

    if high_risk:
        return RiskLevel.HIGH
    if medium_risk:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW
