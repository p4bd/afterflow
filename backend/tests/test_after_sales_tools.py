"""Tests for the local AfterFlow demo tools."""

import json
from pathlib import Path

import yaml
from langchain_core.tools import BaseTool

from app.after_sales.tools import (
    evaluate_after_sales_case_tool,
    evaluate_reverse_fulfillment_tool,
    get_customer_risk_context_tool,
    get_logistics_evidence_tool,
    get_order_context_tool,
    get_payment_context_tool,
    get_policy_snapshot_tool,
    get_replacement_inventory_tool,
    get_reverse_fulfillment_costs_tool,
    scan_after_sales_operations_tool,
)
from deerflow.reflection.resolvers import resolve_variable


def _invoke(tool, **args):
    return json.loads(tool.invoke(args))


def test_read_tools_build_a_traceable_case_context():
    order = _invoke(get_order_context_tool, order_id="ORDER-1001")
    payment = _invoke(get_payment_context_tool, order_id="ORDER-1001")
    logistics = _invoke(get_logistics_evidence_tool, order_id="ORDER-1001")
    risk = _invoke(get_customer_risk_context_tool, customer_id=order["customer_id"])
    policy = _invoke(get_policy_snapshot_tool, region=order["region"])

    assert order["item_paid"] == 89_900
    assert payment["refundable_balance"] == 90_900
    assert logistics["proof_of_delivery"] is False
    assert risk["not_received_claims_180d"] == 0
    assert policy["policy_id"] == "AFTER-SALES-CN"


def test_unknown_business_record_returns_stable_error():
    result = _invoke(get_order_context_tool, order_id="ORDER-NOT-FOUND")

    assert result == {"error": "ORDER_NOT_FOUND", "order_id": "ORDER-NOT-FOUND"}


def test_evaluate_tool_uses_deterministic_engine():
    result = _invoke(
        evaluate_after_sales_case_tool,
        order_id="ORDER-1001",
        issue_type="delivery_not_received",
        operator_refund_limit=20_000,
        visual_evidence_confirmed=False,
    )

    assert result["eligibility"] == "eligible_with_approval"
    assert result["refund_amount"] == 90_900
    assert result["approval_required"] is True
    assert result["decision_source"] == "deterministic_policy_engine"


def test_reverse_tools_use_inventory_cost_and_confirmed_visual_evidence():
    inventory = _invoke(get_replacement_inventory_tool, order_id="ORDER-1003")
    costs = _invoke(get_reverse_fulfillment_costs_tool, order_id="ORDER-1003")
    result = _invoke(
        evaluate_reverse_fulfillment_tool,
        order_id="ORDER-1003",
        issue_type="damaged_item",
        customer_preference="refund",
        damage_level="destroyed",
        human_confirmed=True,
        serial_matches=True,
    )

    assert inventory["available"] == 12
    assert costs["return_shipping_cost"] == 1_200
    assert result["action"] == "refund_without_return"


def test_operations_scan_returns_ranked_traceable_alerts():
    result = _invoke(scan_after_sales_operations_tool)

    assert result["window"] == "current_30d_vs_previous_30d"
    assert result["alerts"][0]["severity"] == "high"
    assert {alert["dimension"] for alert in result["alerts"]} == {"sku", "carrier", "warehouse"}


def test_example_config_registers_twelve_loadable_after_sales_tools():
    config = yaml.safe_load((Path(__file__).parents[2] / "config.example.yaml").read_text(encoding="utf-8"))
    tools = [item for item in config["tools"] if item.get("group") == "after-sales"]

    assert len(tools) == 12
    assert "after-sales" in {group["name"] for group in config["tool_groups"]}
    for item in tools:
        resolved = resolve_variable(item["use"])
        assert isinstance(resolved, BaseTool)
