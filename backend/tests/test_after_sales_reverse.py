"""Tests for deterministic reverse-fulfillment planning."""

import pytest
from pydantic import ValidationError

from app.after_sales.reverse import (
    CustomerPreference,
    ReverseAction,
    ReverseInput,
    ReverseOutcome,
    VisualEvidence,
    decide_reverse_fulfillment,
)
from app.after_sales.schemas import IssueType


def _case(**overrides):
    data = {
        "issue_type": IssueType.DAMAGED_ITEM,
        "item_value": 32_800,
        "refund_amount": 32_800,
        "return_shipping_cost": 1_200,
        "handling_cost": 600,
        "expected_recovery_value": 1_000,
        "replacement_unit_cost": 18_000,
        "replacement_shipping_cost": 800,
        "replacement_inventory": 5,
        "customer_preference": CustomerPreference.REFUND,
        "visual_evidence": VisualEvidence(damage_level="destroyed", human_confirmed=True),
    }
    data.update(overrides)
    return ReverseInput(**data)


def test_destroyed_low_residual_item_refunds_without_wasteful_return():
    result = decide_reverse_fulfillment(_case())

    assert result.outcome is ReverseOutcome.DECIDED
    assert result.action is ReverseAction.REFUND_WITHOUT_RETURN
    assert result.estimated_resolution_cost == 32_800
    assert "return_cost_exceeds_recovery" in result.signals


def test_recoverable_item_uses_return_and_refund_when_recovery_covers_reverse_cost():
    result = decide_reverse_fulfillment(
        _case(
            visual_evidence=VisualEvidence(damage_level="minor", human_confirmed=True),
            expected_recovery_value=20_000,
        )
    )

    assert result.action is ReverseAction.RETURN_AND_REFUND
    assert result.estimated_resolution_cost == 14_600


def test_wrong_item_prefers_replacement_when_inventory_exists():
    result = decide_reverse_fulfillment(
        _case(
            issue_type=IssueType.WRONG_ITEM,
            customer_preference=CustomerPreference.REPLACEMENT,
            expected_recovery_value=2_000,
            visual_evidence=VisualEvidence(damage_level="none", serial_matches=False, human_confirmed=True),
        )
    )

    assert result.action is ReverseAction.REPLACE_AFTER_RETURN
    assert result.estimated_resolution_cost == 18_600


def test_replacement_request_falls_back_when_inventory_is_empty():
    result = decide_reverse_fulfillment(
        _case(
            customer_preference=CustomerPreference.REPLACEMENT,
            replacement_inventory=0,
            expected_recovery_value=20_000,
        )
    )

    assert result.action is ReverseAction.RETURN_AND_REFUND
    assert "replacement_out_of_stock" in result.signals


def test_unconfirmed_visual_evidence_stops_for_human_review():
    result = decide_reverse_fulfillment(_case(visual_evidence=VisualEvidence(damage_level="major", human_confirmed=False)))

    assert result.outcome is ReverseOutcome.NEEDS_EVIDENCE
    assert result.action is ReverseAction.MANUAL_REVIEW
    assert result.missing_evidence == ["human_confirmed_visual_evidence"]


def test_reverse_costs_reject_negative_money():
    with pytest.raises(ValidationError):
        _case(return_shipping_cost=-1)
