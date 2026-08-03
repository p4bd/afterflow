"""Deterministic reverse-fulfillment decision and cost model."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from .schemas import IssueType


class CustomerPreference(StrEnum):
    REFUND = "refund"
    REPLACEMENT = "replacement"


class ReverseAction(StrEnum):
    REFUND_WITHOUT_RETURN = "refund_without_return"
    RETURN_AND_REFUND = "return_and_refund"
    REPLACE_AFTER_RETURN = "replace_after_return"
    MANUAL_REVIEW = "manual_review"


class ReverseOutcome(StrEnum):
    DECIDED = "decided"
    NEEDS_EVIDENCE = "needs_evidence"
    UNSUPPORTED = "unsupported"


class VisualEvidence(BaseModel):
    damage_level: Literal["none", "minor", "major", "destroyed"]
    serial_matches: bool | None = None
    human_confirmed: bool = False


class ReverseInput(BaseModel):
    issue_type: IssueType
    item_value: int = Field(ge=0)
    refund_amount: int = Field(ge=0)
    return_shipping_cost: int = Field(ge=0)
    handling_cost: int = Field(ge=0)
    expected_recovery_value: int = Field(ge=0)
    replacement_unit_cost: int = Field(ge=0)
    replacement_shipping_cost: int = Field(ge=0)
    replacement_inventory: int = Field(ge=0)
    customer_preference: CustomerPreference
    visual_evidence: VisualEvidence | None = None


class ReverseDecision(BaseModel):
    outcome: ReverseOutcome
    action: ReverseAction
    estimated_resolution_cost: int = Field(ge=0)
    requires_return: bool
    signals: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


def decide_reverse_fulfillment(case: ReverseInput) -> ReverseDecision:
    if case.issue_type not in {IssueType.DAMAGED_ITEM, IssueType.WRONG_ITEM, IssueType.QUALITY_ISSUE}:
        return ReverseDecision(
            outcome=ReverseOutcome.UNSUPPORTED,
            action=ReverseAction.MANUAL_REVIEW,
            estimated_resolution_cost=0,
            requires_return=False,
            signals=["issue_not_supported_for_reverse_fulfillment"],
        )
    if case.visual_evidence is None or not case.visual_evidence.human_confirmed:
        return ReverseDecision(
            outcome=ReverseOutcome.NEEDS_EVIDENCE,
            action=ReverseAction.MANUAL_REVIEW,
            estimated_resolution_cost=0,
            requires_return=False,
            missing_evidence=["human_confirmed_visual_evidence"],
        )

    signals: list[str] = []
    reverse_cost = case.return_shipping_cost + case.handling_cost
    return_refund_cost = max(0, case.refund_amount + reverse_cost - case.expected_recovery_value)

    if case.customer_preference is CustomerPreference.REPLACEMENT:
        if case.replacement_inventory > 0:
            replacement_cost = max(
                0,
                case.replacement_unit_cost + case.replacement_shipping_cost + reverse_cost - case.expected_recovery_value,
            )
            return ReverseDecision(
                outcome=ReverseOutcome.DECIDED,
                action=ReverseAction.REPLACE_AFTER_RETURN,
                estimated_resolution_cost=replacement_cost,
                requires_return=True,
                signals=["replacement_inventory_available"],
            )
        signals.append("replacement_out_of_stock")

    if case.expected_recovery_value >= reverse_cost:
        return ReverseDecision(
            outcome=ReverseOutcome.DECIDED,
            action=ReverseAction.RETURN_AND_REFUND,
            estimated_resolution_cost=return_refund_cost,
            requires_return=True,
            signals=signals + ["recovery_covers_reverse_cost"],
        )
    return ReverseDecision(
        outcome=ReverseOutcome.DECIDED,
        action=ReverseAction.REFUND_WITHOUT_RETURN,
        estimated_resolution_cost=case.refund_amount,
        requires_return=False,
        signals=signals + ["return_cost_exceeds_recovery"],
    )
