"""Stable business contracts for deterministic after-sales decisions."""

from enum import StrEnum

from pydantic import BaseModel, Field


class IssueType(StrEnum):
    DELIVERY_NOT_RECEIVED = "delivery_not_received"
    DAMAGED_ITEM = "damaged_item"
    WRONG_ITEM = "wrong_item"
    QUALITY_ISSUE = "quality_issue"
    REFUND_AMOUNT_DISPUTE = "refund_amount_dispute"


class Eligibility(StrEnum):
    ELIGIBLE = "eligible"
    ELIGIBLE_WITH_APPROVAL = "eligible_with_approval"
    INELIGIBLE = "ineligible"
    NEEDS_EVIDENCE = "needs_evidence"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskTier(StrEnum):
    AUTO = "auto"                        # no human sign-off (subject to operator limit)
    REVIEW = "review"                    # one reviewer
    SUPERVISOR = "supervisor"            # supervisor; self-approval forbidden
    FOUR_EYES = "four_eyes"              # two distinct approvers


class ResolutionAction(StrEnum):
    REFUND_ORIGINAL_PAYMENT = "refund_original_payment"
    RETURN_AND_REFUND = "return_and_refund"
    MANUAL_REVIEW = "manual_review"


class OrderContext(BaseModel):
    order_id: str = Field(min_length=1)
    item_paid: int = Field(ge=0, description="Item amount paid, in minor currency units")
    shipping_paid: int = Field(ge=0, description="Shipping amount paid, in minor currency units")


class PaymentContext(BaseModel):
    refundable_balance: int = Field(ge=0, description="Remaining refundable amount")


class LogisticsEvidence(BaseModel):
    status: str = Field(min_length=1)
    proof_of_delivery: bool


class CustomerRiskContext(BaseModel):
    not_received_claims_180d: int = Field(default=0, ge=0)
    refund_cases_180d: int = Field(default=0, ge=0)


class PolicySnapshot(BaseModel):
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    covered_issues: set[IssueType]
    refund_shipping_issues: set[IssueType] = Field(default_factory=set)
    return_required_issues: set[IssueType] = Field(default_factory=set)
    manual_review_amount: int = Field(ge=0)
    high_value_amount: int = Field(ge=0)


class DecisionInput(BaseModel):
    issue_type: IssueType
    order: OrderContext
    payment: PaymentContext
    logistics: LogisticsEvidence | None = None
    customer_risk: CustomerRiskContext = Field(default_factory=CustomerRiskContext)
    policy: PolicySnapshot
    operator_refund_limit: int = Field(ge=0)
    visual_evidence_confirmed: bool = False
    # Additional risk-scorecard signals (all optional; absent = neutral).
    sign_receipt_hours: int | None = None
    account_age_days: int | None = None
    historical_refund_rate: float | None = None
    address_changes_30d: int = 0
    device_reuse: bool = False


class DecisionResult(BaseModel):
    eligibility: Eligibility
    action: ResolutionAction
    refund_amount: int = Field(ge=0)
    risk_level: RiskLevel
    reason_code: str
    approval_required: bool
    approval_reasons: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    # Transparent risk-scorecard output: a 0-100 score, an intervention tier,
    # and the per-signal point breakdown (explainable to reviewers/customers).
    risk_score: int = Field(default=0, ge=0, le=100)
    risk_tier: RiskTier = RiskTier.AUTO
    risk_signals: list[dict] = Field(default_factory=list)
