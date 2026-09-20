"""Minimal complaint intake and deterministic case re-evaluation."""

import re
from datetime import UTC, datetime

from .reverse import CustomerPreference
from .schemas import IssueType
from .workflow import run_case_evaluation

_ORDER_RE = re.compile(r"\bORDER-\d+\b", re.IGNORECASE)
_ISSUE_TERMS = (
    (IssueType.DELIVERY_NOT_RECEIVED, ("没收到", "未收到", "没有收到", "not received")),
    (IssueType.WRONG_ITEM, ("错发", "发错", "wrong item")),
    (IssueType.DAMAGED_ITEM, ("破损", "损坏", "碎了", "坏了", "damaged")),
    (IssueType.QUALITY_ISSUE, ("没声音", "故障", "质量", "不能用", "quality")),
    (IssueType.REFUND_AMOUNT_DISPUTE, ("退款金额", "少退", "金额争议")),
)
_ISSUE_ALIASES = {
    "not_received": IssueType.DELIVERY_NOT_RECEIVED.value,
    "delivery_issue": IssueType.DELIVERY_NOT_RECEIVED.value,
    "damaged": IssueType.DAMAGED_ITEM.value,
    "wrong": IssueType.WRONG_ITEM.value,
    "quality": IssueType.QUALITY_ISSUE.value,
    "refund_dispute": IssueType.REFUND_AMOUNT_DISPUTE.value,
}


def normalize_issue_type(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    return value if value in {item.value for item in IssueType} else _ISSUE_ALIASES.get(value)


def extract_intake(complaint_text: str) -> dict:
    text = complaint_text.strip()
    order_ids = list(dict.fromkeys(match.upper() for match in _ORDER_RE.findall(text)))
    issue = next((kind.value for kind, terms in _ISSUE_TERMS if any(term in text.lower() for term in terms)), None)
    expectation = None
    if any(term in text.lower() for term in ("换一个", "换货", "补发", "replacement", "replace")):
        expectation = CustomerPreference.REPLACEMENT.value
    elif any(term in text.lower() for term in ("退款", "退钱", "refund")):
        expectation = CustomerPreference.REFUND.value
    return {
        "order_id": order_ids[0] if len(order_ids) == 1 else None,
        "issue_type": normalize_issue_type(issue),
        "customer_expectation": expectation,
    }


def evaluate_intake(
    *,
    complaint_text: str,
    order_id: str | None,
    issue_type: str | None,
    customer_expectation: str | None,
    operator_refund_limit: int,
    visual_evidence_confirmed: bool = False,
    damage_level: str | None = None,
    serial_matches: bool | None = None,
    revision: int = 1,
) -> dict:
    missing = [name for name, value in (("order_id", order_id), ("issue_type", issue_type)) if not value]
    intake = {
        "complaint_text": complaint_text,
        "customer_expectation": customer_expectation,
        "visual_evidence_confirmed": visual_evidence_confirmed,
        "damage_level": damage_level,
        "serial_matches": serial_matches,
    }
    if missing:
        return {
            "status": "awaiting_clarification",
            "next_step": "clarify_" + missing[0],
            "decision": {},
            "evidence": {"intake": intake, "claims": [complaint_text], "facts": [], "sources": [], "missing": missing, "conflicts": [], "revision": revision},
            "reply_draft": "请补充订单号和具体问题后，我会继续处理。" if len(missing) > 1 else f"请补充{('订单号' if missing[0] == 'order_id' else '具体问题')}后，我会继续处理。",
        }

    state = run_case_evaluation(
        order_id=order_id,
        issue_type=issue_type,
        operator_refund_limit=operator_refund_limit,
        visual_evidence_confirmed=visual_evidence_confirmed,
        customer_preference=customer_expectation if customer_expectation == CustomerPreference.REPLACEMENT.value else None,
        damage_level=damage_level,
        human_confirmed=visual_evidence_confirmed,
        serial_matches=serial_matches,
    )
    if state.get("error"):
        return {
            "status": "awaiting_clarification",
            "next_step": "verify_order",
            "decision": {},
            "evidence": {"intake": intake, "claims": [complaint_text], "facts": [], "sources": [], "missing": ["valid_order"], "conflicts": [], "revision": revision},
            "reply_draft": "未找到该订单，请核对订单号。",
        }

    snapshot_at = datetime.now(UTC).isoformat()
    raw = state["evidence"]
    facts = [{"source": source, "value": value, "acquired_at": snapshot_at} for source, value in raw.items() if value is not None and source not in {"visual_evidence_confirmed"}]
    conflicts = []
    if issue_type == IssueType.DELIVERY_NOT_RECEIVED.value and raw.get("logistics", {}).get("proof_of_delivery"):
        conflicts.append("customer_reports_not_received_but_carrier_has_proof_of_delivery")

    reverse = state.get("reverse_decision")
    if reverse is not None:
        missing_evidence = list(reverse.missing_evidence)
        decision = {"route": "reverse", "reverse": reverse.model_dump(mode="json")}
        status = "awaiting_evidence" if missing_evidence else "decided"
        next_step = "confirm_visual_evidence" if missing_evidence else "create_action"
        reply = "需要客服人工确认图片证据后再继续。" if missing_evidence else "已根据库存、残值与履约成本形成处理方案，待提交审批。"
    else:
        refund = state["decision"]
        missing_evidence = list(refund.missing_evidence)
        decision = refund.model_dump(mode="json")
        status = "awaiting_evidence" if missing_evidence or conflicts else "decided"
        next_step = "supplement_evidence" if status == "awaiting_evidence" else "create_action"
        reply = "还需要补充证据或核对签收差异，当前不会执行退款。" if status == "awaiting_evidence" else "已完成规则评估，处理方案待提交审批。"

    return {
        "status": status,
        "next_step": next_step,
        "decision": decision,
        "evidence": {
            **raw,
            "intake": intake,
            "claims": [complaint_text],
            "facts": facts,
            "sources": sorted({fact["source"] for fact in facts}),
            "missing": missing_evidence,
            "conflicts": conflicts,
            "snapshot_at": snapshot_at,
            "revision": revision,
        },
        "reply_draft": reply,
    }
