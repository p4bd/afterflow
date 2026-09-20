"""Realistic business-flow scenarios: narrative -> issue -> decision -> terminal state.

Unlike the 150-case parameter-sweep corpus, these cases start from a realistic
customer complaint (Chinese), normalize it into an issue type the way the agent
would, feed the case facts + risk-scorecard signals into the deterministic
pipeline, and assert the TERMINAL decision — the same philosophy as τ-bench
(check the end state, not the chat log).
"""

import json
from pathlib import Path

import pytest

from app.after_sales.decision import decide_resolution
from app.after_sales.schemas import DecisionInput, IssueType

SCENARIOS = json.loads((Path(__file__).parent / "fixtures" / "after_sales_scenarios.json").read_text(encoding="utf-8"))

ISSUE_KEYWORDS: dict[IssueType, tuple[str, ...]] = {
    IssueType.DELIVERY_NOT_RECEIVED: ("没收到", "未收到", "没到货", "快递没来", "丢件", "物流信息"),
    IssueType.DAMAGED_ITEM: ("破损", "坏了", "碎了", "压扁", "磕碰"),
    IssueType.WRONG_ITEM: ("错发", "发错", "发来个", "不是我要的"),
    IssueType.QUALITY_ISSUE: ("质量问题", "有点问题", "瑕疵", "亮线", "充不进", "不好用"),
    IssueType.REFUND_AMOUNT_DISPUTE: ("退少了", "退款金额", "扣多了"),
}


def _normalize_issue(scenario: str) -> IssueType:
    for issue, keywords in ISSUE_KEYWORDS.items():
        if any(keyword in scenario for keyword in keywords):
            return issue
    raise ValueError(f"unrecognized issue in scenario: {scenario[:30]}")


def _decide(scenario: dict) -> dict:
    ctx = scenario["context"]
    issue = _normalize_issue(scenario["scenario"])
    result = decide_resolution(
        DecisionInput(
            issue_type=issue,
            order={"order_id": scenario["id"], "item_paid": ctx["item"], "shipping_paid": ctx["shipping"]},
            payment={"refundable_balance": ctx["balance"]},
            logistics=({"status": "delivered" if ctx["pod"] else "in_transit", "proof_of_delivery": ctx["pod"]} if ctx["logistics_present"] else None),
            customer_risk={"not_received_claims_180d": ctx["claims"], "refund_cases_180d": ctx["refund_cases"]},
            policy={
                "policy_id": "AFTER-SALES-CN",
                "version": "2026.07",
                "covered_issues": set(IssueType),
                "refund_shipping_issues": {IssueType.DELIVERY_NOT_RECEIVED},
                "return_required_issues": {IssueType.DAMAGED_ITEM, IssueType.WRONG_ITEM},
                "manual_review_amount": 100_000,
                "high_value_amount": 50_000,
            },
            operator_refund_limit=ctx["operator_limit"],
            visual_evidence_confirmed=ctx.get("visual_confirmed", False),
            sign_receipt_hours=ctx.get("sign_receipt_hours"),
            account_age_days=ctx.get("account_age_days"),
            historical_refund_rate=ctx.get("refund_rate"),
            address_changes_30d=ctx.get("address_changes", 0),
            device_reuse=ctx.get("device_reuse", False),
        )
    ).model_dump(mode="json")
    return result


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_realistic_scenario_terminal_decision(scenario):
    actual = _decide(scenario)

    for field, expected in scenario["expect"].items():
        assert actual[field] == expected, f"{scenario['id']}: field {field} = {actual.get(field)!r}, expected {expected!r}\n  scenario: {scenario['scenario']}"


def test_scenarios_cover_a_spread_of_tiers():
    tiers = {s["expect"]["risk_tier"] for s in SCENARIOS}
    levels = {s["expect"]["risk_level"] for s in SCENARIOS}
    assert tiers >= {"auto", "review", "supervisor", "four_eyes"}
    assert levels == {"low", "medium", "high"}
    assert len(SCENARIOS) >= 10
