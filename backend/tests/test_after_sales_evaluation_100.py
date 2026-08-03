"""Fixed 100-case evaluation spanning refund, reverse, and operations decisions."""

import json
from pathlib import Path

import pytest

from app.after_sales.decision import decide_resolution
from app.after_sales.reverse import ReverseInput, VisualEvidence, decide_reverse_fulfillment
from app.after_sales.risk_ops import MetricBucket, detect_after_sales_anomalies
from app.after_sales.schemas import DecisionInput

CASES = json.loads((Path(__file__).parent / "fixtures" / "after_sales_evaluation_100.json").read_text(encoding="utf-8"))


def _evaluate(case):
    values = case["input"]
    if case["kind"] == "refund":
        issue = values["issue"]
        return decide_resolution(
            DecisionInput(
                issue_type=issue,
                order={"order_id": case["id"], "item_paid": values["item"], "shipping_paid": values["shipping"]},
                payment={"refundable_balance": values["balance"]},
                logistics=({"status": "delivered" if values["pod"] else "in_transit", "proof_of_delivery": values["pod"]} if values["logistics_present"] else None),
                customer_risk={"not_received_claims_180d": values["claims"], "refund_cases_180d": 0},
                policy={
                    "policy_id": "EVAL",
                    "version": "1",
                    "covered_issues": [issue] if values["covered"] else [],
                    "refund_shipping_issues": ["delivery_not_received"],
                    "return_required_issues": ["damaged_item", "wrong_item"],
                    "manual_review_amount": values["manual"],
                    "high_value_amount": values["high"],
                },
                operator_refund_limit=values["operator"],
                visual_evidence_confirmed=values["visual"],
            )
        ).model_dump(mode="json")
    if case["kind"] == "reverse":
        return decide_reverse_fulfillment(
            ReverseInput(
                issue_type=values["issue"],
                item_value=values["refund"],
                refund_amount=values["refund"],
                return_shipping_cost=values["return_shipping"],
                handling_cost=values["handling"],
                expected_recovery_value=values["recovery"],
                replacement_unit_cost=7_000,
                replacement_shipping_cost=600,
                replacement_inventory=values["inventory"],
                customer_preference=values["preference"],
                visual_evidence=VisualEvidence(damage_level="major", human_confirmed=values["human"]),
            )
        ).model_dump(mode="json")
    alerts = detect_after_sales_anomalies(
        [
            MetricBucket(
                dimension=values["dimension"],
                value=values["value"],
                issue_type="quality_issue",
                current_orders=values["orders"],
                current_issue_cases=values["issue_cases"],
                previous_orders=values["prev_orders"],
                previous_issue_cases=values["prev_cases"],
                current_loss_amount=values["loss"],
            )
        ]
    )
    return {"alert": bool(alerts), "severity": alerts[0].severity if alerts else None}


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_evaluation_case(case):
    actual = _evaluate(case)

    for field, expected in case["expect"].items():
        assert actual[field] == expected


def test_evaluation_suite_has_100_unique_cross_domain_cases():
    ids = [case["id"] for case in CASES]

    assert len(ids) == 100
    assert len(set(ids)) == 100
    assert {case["kind"] for case in CASES} == {"refund", "reverse", "operations"}
