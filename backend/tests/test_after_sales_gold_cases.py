"""Regression suite for the interview/demo AfterFlow business scenarios."""

import json
from pathlib import Path

import pytest

from app.after_sales.decision import decide_resolution
from app.after_sales.schemas import DecisionInput

GOLD_CASES = json.loads((Path(__file__).parent / "fixtures" / "after_sales_gold_cases.json").read_text(encoding="utf-8"))


def _decision(case: dict) -> dict:
    issue = case["issue"]
    payload = {
        "issue_type": issue,
        "order": {
            "order_id": case["id"],
            "item_paid": case.get("item_paid", 10_000),
            "shipping_paid": case.get("shipping_paid", 500),
        },
        "payment": {"refundable_balance": case.get("refundable_balance", 10_500)},
        "logistics": ({"status": "delivered" if case.get("pod") else "in_transit", "proof_of_delivery": case.get("pod", False)} if case.get("logistics_present", True) else None),
        "customer_risk": {"not_received_claims_180d": case.get("claim_count", 0), "refund_cases_180d": 0},
        "policy": {
            "policy_id": "GOLD-POLICY",
            "version": "1.0",
            "covered_issues": [issue] if case.get("covered", True) else [],
            "refund_shipping_issues": [issue] if case.get("refund_shipping", False) else [],
            "return_required_issues": [issue] if case.get("return_required", False) else [],
            "manual_review_amount": case.get("manual_review_amount", 100_000),
            "high_value_amount": case.get("high_value_amount", 50_000),
        },
        "operator_refund_limit": case.get("operator_limit", 20_000),
        "visual_evidence_confirmed": case.get("visual", True),
    }
    return decide_resolution(DecisionInput(**payload)).model_dump(mode="json")


@pytest.mark.parametrize("case", GOLD_CASES, ids=[case["id"] for case in GOLD_CASES])
def test_gold_case(case):
    result = _decision(case)

    for field, expected in case["expect"].items():
        assert result[field] == expected


def test_gold_suite_has_30_unique_scenarios():
    ids = [case["id"] for case in GOLD_CASES]

    assert len(ids) == 30
    assert len(set(ids)) == 30
