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
    RESEND_WITHOUT_RETURN = "resend_without_return"
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


class ReturnGrade(StrEnum):
    A = "a"  # like new -> restock
    B = "b"  # refurbishable -> refurbish
    C = "c"  # damaged -> liquidate
    D = "d"  # destroyed -> dispose


class ReverseDecision(BaseModel):
    outcome: ReverseOutcome
    action: ReverseAction
    estimated_resolution_cost: int = Field(ge=0)
    requires_return: bool
    signals: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    # Quality-inspection grading (mirrors Amazon Grade & Resell / ReverseLogix):
    # set when a return will actually be received and inspected.
    grade: ReturnGrade | None = None
    disposition: str | None = None


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

    # Return-abuse hold: the returned item's serial number does not match the
    # one shipped (a classic swap/return-fraud signal) — except for wrong-item
    # cases, where the mismatch is expected because the customer DID receive a
    # different item. Such returns are frozen for manual review, not auto-routed.
    if case.visual_evidence is not None and case.visual_evidence.serial_matches is False and case.issue_type is not IssueType.WRONG_ITEM:
        return ReverseDecision(
            outcome=ReverseOutcome.DECIDED,
            action=ReverseAction.MANUAL_REVIEW,
            estimated_resolution_cost=0,
            requires_return=True,
            signals=["serial_mismatch_hold"],
        )

    # The recovery value is discounted by how damaged the item is — a destroyed
    # unit has no resale/refurbish value, a minor-scratch one keeps most of it.
    # This is what makes the grade REAL: the disposition route flows from the
    # economics, not from a label pasted on the result.
    grade, disposition = _grade(case.visual_evidence.damage_level)
    recovery = round(case.expected_recovery_value * RECOVERY_FACTOR[case.visual_evidence.damage_level])

    signals: list[str] = []
    reverse_cost = case.return_shipping_cost + case.handling_cost
    return_refund_cost = max(0, case.refund_amount + reverse_cost - recovery)

    if case.customer_preference is CustomerPreference.REPLACEMENT:
        if case.replacement_inventory > 0:
            if recovery < reverse_cost:
                # The return is economically pointless: recovery does not cover
                # the reverse cost, so resend a replacement without a return
                # (returnless resend), matching the industry returnless-refund
                # threshold logic applied to replacements.
                resend_cost = max(0, case.replacement_unit_cost + case.replacement_shipping_cost)
                return ReverseDecision(
                    outcome=ReverseOutcome.DECIDED,
                    action=ReverseAction.RESEND_WITHOUT_RETURN,
                    estimated_resolution_cost=resend_cost,
                    requires_return=False,
                    signals=["replacement_inventory_available", "return_cost_exceeds_recovery"],
                    grade=grade,
                    disposition=disposition,
                )
            replacement_cost = max(
                0,
                case.replacement_unit_cost + case.replacement_shipping_cost + reverse_cost - recovery,
            )
            return ReverseDecision(
                outcome=ReverseOutcome.DECIDED,
                action=ReverseAction.REPLACE_AFTER_RETURN,
                estimated_resolution_cost=replacement_cost,
                requires_return=True,
                signals=["replacement_inventory_available"],
                grade=grade,
                disposition=disposition,
            )
        signals.append("replacement_out_of_stock")

    if recovery >= reverse_cost:
        return ReverseDecision(
            outcome=ReverseOutcome.DECIDED,
            action=ReverseAction.RETURN_AND_REFUND,
            estimated_resolution_cost=return_refund_cost,
            requires_return=True,
            signals=signals + ["recovery_covers_reverse_cost"],
            grade=grade,
            disposition=disposition,
        )
    return ReverseDecision(
        outcome=ReverseOutcome.DECIDED,
        action=ReverseAction.REFUND_WITHOUT_RETURN,
        estimated_resolution_cost=case.refund_amount,
        requires_return=False,
        signals=signals + ["return_cost_exceeds_recovery"],
        grade=grade,
        disposition=disposition,
    )


# How much of the SKU's nominal recovery value survives a given damage level.
# A "destroyed" unit recovers nothing; "like new" keeps the full value. This
# factor ties the quality grade to the economics (a destroyed item is disposed,
# a like-new one is restocked).
RECOVERY_FACTOR: dict[str, float] = {
    "none": 1.0,
    "minor": 0.7,
    "major": 0.4,
    "destroyed": 0.0,
}


def _grade(damage_level: str) -> tuple[ReturnGrade, str]:
    """Map visual damage level to a quality grade + disposition route.

    Mirrors Amazon Grade & Resell / ReverseLogix: like-new -> restock,
    minor -> refurbish, major -> liquidate, destroyed -> dispose.
    """
    if damage_level == "none":
        return ReturnGrade.A, "restock"
    if damage_level == "minor":
        return ReturnGrade.B, "refurbish"
    if damage_level == "major":
        return ReturnGrade.C, "liquidate"
    return ReturnGrade.D, "dispose"
