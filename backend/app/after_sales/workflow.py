"""LangGraph orchestration for AfterFlow.

AfterFlow depends on LangGraph at the orchestration layer: the case evaluation
(which decision path applies) and the reverse-fulfillment disposition lifecycle
(how a returned item moves to settlement) are explicit StateGraphs, not ad-hoc
if/else. The money-safety invariants stay in the domain functions the graph
nodes call (decide_resolution / decide_reverse_fulfillment / advance_disposition);
the graph makes the orchestration visible, testable, and extensible.
"""

from datetime import datetime
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .decision import decide_resolution
from .mock_data import (
    CUSTOMER_PROFILES,
    CUSTOMER_RISK,
    INVENTORY,
    LOGISTICS,
    ORDERS,
    PAYMENTS,
    POLICIES,
    REVERSE_COSTS,
    SIGN_RECEIPT_HOURS,
)
from .reverse import (
    CustomerPreference,
    ReverseDecision,
    ReverseInput,
    VisualEvidence,
    decide_reverse_fulfillment,
)
from .reverse_execution import (
    DispositionStatus,
    ReverseDisposition,
    advance_disposition,
    build_disposition,
    mark_received,
)
from .schemas import (
    CustomerRiskContext,
    DecisionInput,
    DecisionResult,
    IssueType,
    LogisticsEvidence,
    OrderContext,
    PaymentContext,
    PolicySnapshot,
)

REVERSE_ISSUES = {IssueType.DAMAGED_ITEM, IssueType.WRONG_ITEM, IssueType.QUALITY_ISSUE}


class CaseEvalState(TypedDict, total=False):
    order_id: str
    issue_type: str
    operator_refund_limit: int
    visual_evidence_confirmed: bool
    # Optional reverse-evaluation context (set by the reverse tool).
    customer_preference: str | None
    damage_level: str | None
    human_confirmed: bool | None
    serial_matches: bool | None
    # Produced by nodes.
    case: DecisionInput
    evidence: dict
    error: dict | None
    route: str
    decision: DecisionResult | None
    reverse_decision: ReverseDecision | None


def _build_context(state: CaseEvalState) -> CaseEvalState:
    order_id = state["order_id"]
    issue_type = state["issue_type"]
    order = ORDERS.get(order_id)
    payment = PAYMENTS.get(order_id)
    if order is None:
        return {"error": {"error": "ORDER_NOT_FOUND", "order_id": order_id}, "route": "error"}
    if payment is None:
        return {"error": {"error": "PAYMENT_NOT_FOUND", "order_id": order_id}, "route": "error"}
    policy_data = POLICIES.get(order["region"])
    if policy_data is None:
        return {"error": {"error": "POLICY_NOT_FOUND", "region": order["region"]}, "route": "error"}

    logistics_data = LOGISTICS.get(order_id)
    risk_data = CUSTOMER_RISK.get(order["customer_id"], {})
    profile = CUSTOMER_PROFILES.get(order["customer_id"], {})
    case = DecisionInput(
        issue_type=IssueType(issue_type),
        order=OrderContext(**order),
        payment=PaymentContext(**payment),
        logistics=LogisticsEvidence(**logistics_data) if logistics_data else None,
        customer_risk=CustomerRiskContext(**risk_data),
        policy=PolicySnapshot(**policy_data),
        operator_refund_limit=state["operator_refund_limit"],
        visual_evidence_confirmed=state["visual_evidence_confirmed"],
        sign_receipt_hours=SIGN_RECEIPT_HOURS.get(order_id),
        account_age_days=profile.get("account_age_days"),
        historical_refund_rate=profile.get("historical_refund_rate"),
        address_changes_30d=profile.get("address_changes_30d", 0),
        device_reuse=profile.get("device_reuse", False),
    )
    evidence = {
        "order": order,
        "payment": payment,
        "logistics": logistics_data,
        "customer_risk": risk_data,
        "policy": policy_data,
        "inventory": INVENTORY.get(order["sku"]),
        "reverse_costs": REVERSE_COSTS.get(order["sku"]),
        "visual_evidence_confirmed": state["visual_evidence_confirmed"],
    }
    return {"case": case, "evidence": evidence}


def _decide_refund(state: CaseEvalState) -> CaseEvalState:
    return {"decision": decide_resolution(state["case"]), "route": "refund"}


def _decide_reverse(state: CaseEvalState) -> CaseEvalState:
    order = state["evidence"]["order"]
    payment = state["evidence"]["payment"]
    inventory = state["evidence"].get("inventory")
    costs = state["evidence"].get("reverse_costs")
    if inventory is None or costs is None:
        return {"error": {"error": "REVERSE_CONTEXT_NOT_FOUND", "sku": order["sku"]}, "route": "error"}
    case = ReverseInput(
        issue_type=IssueType(state["issue_type"]),
        item_value=order["item_paid"],
        refund_amount=min(order["item_paid"], payment["refundable_balance"]),
        return_shipping_cost=costs["return_shipping_cost"],
        handling_cost=costs["handling_cost"],
        expected_recovery_value=costs["expected_recovery_value"],
        replacement_unit_cost=inventory["replacement_unit_cost"],
        replacement_shipping_cost=costs["replacement_shipping_cost"],
        replacement_inventory=inventory["available"],
        customer_preference=CustomerPreference(state["customer_preference"]),
        visual_evidence=VisualEvidence(
            damage_level=state.get("damage_level") or "none",
            serial_matches=state.get("serial_matches"),
            human_confirmed=bool(state.get("human_confirmed", False)),
        ),
    )
    return {"reverse_decision": decide_reverse_fulfillment(case), "route": "reverse"}


def _route_case(state: CaseEvalState) -> str:
    """Route: errors terminate; reverse issues with reverse context go to reverse."""
    if state.get("error"):
        return "error"
    issue = IssueType(state["issue_type"])
    if issue in REVERSE_ISSUES and state.get("customer_preference"):
        return "reverse"
    return "refund"


def _build_case_graph():
    graph = StateGraph(CaseEvalState)
    graph.add_node("build_context", _build_context)
    graph.add_node("decide_refund", _decide_refund)
    graph.add_node("reverse_decision", _decide_reverse)
    graph.add_edge(START, "build_context")
    graph.add_conditional_edges(
        "build_context",
        _route_case,
        {"reverse": "reverse_decision", "refund": "decide_refund", "error": END},
    )
    graph.add_edge("decide_refund", END)
    graph.add_edge("reverse_decision", END)
    return graph.compile()


def run_case_evaluation(
    *,
    order_id: str,
    issue_type: str,
    operator_refund_limit: int,
    visual_evidence_confirmed: bool = False,
    customer_preference: str | None = None,
    damage_level: str | None = None,
    human_confirmed: bool | None = None,
    serial_matches: bool | None = None,
) -> dict:
    """Run the AfterFlow case-evaluation workflow graph; returns the terminal state."""
    return _CASE_GRAPH.invoke(
        {
            "order_id": order_id,
            "issue_type": issue_type,
            "operator_refund_limit": operator_refund_limit,
            "visual_evidence_confirmed": visual_evidence_confirmed,
            "customer_preference": customer_preference,
            "damage_level": damage_level,
            "human_confirmed": human_confirmed,
            "serial_matches": serial_matches,
        }
    )


# ---- Reverse-fulfillment disposition lifecycle graph ----

class DispositionState(TypedDict, total=False):
    decision: ReverseDecision
    case_id: str
    order_id: str
    sku: str
    received_at: str
    sla_hours: int
    disposition: ReverseDisposition
    route: str
    error: str | None


def _disposition_start(state: DispositionState) -> DispositionState:
    return {
        "disposition": build_disposition(
            case_id=state["case_id"],
            order_id=state["order_id"],
            sku=state["sku"],
            decision=state["decision"],
        )
    }


def _disposition_receive(state: DispositionState) -> DispositionState:
    received_at = state["received_at"]
    if isinstance(received_at, str):
        received_at = datetime.fromisoformat(received_at)
    disposition = mark_received(state["disposition"], received_at=received_at, sla_hours=state.get("sla_hours", 48))
    return {"disposition": disposition}


def _disposition_inspect(state: DispositionState) -> DispositionState:
    status = advance_disposition(state["disposition"].status, DispositionStatus.INSPECTED)
    return {"disposition": state["disposition"].model_copy(update={"status": status})}


def _disposition_settle(state: DispositionState) -> DispositionState:
    disposition = state["disposition"]
    if disposition.status is not DispositionStatus.SETTLED:
        status = advance_disposition(disposition.status, DispositionStatus.SETTLED)
        disposition = disposition.model_copy(update={"status": status})
    route = getattr(state["decision"], "disposition", None) or "restock"
    return {"disposition": disposition, "route": route}


def _disposition_dispose(state: DispositionState) -> DispositionState:
    disposition = state["disposition"]
    if disposition.status is not DispositionStatus.SETTLED:
        status = advance_disposition(disposition.status, DispositionStatus.SETTLED)
        disposition = disposition.model_copy(update={"status": status})
    return {"disposition": disposition, "route": "dispose"}


def _route_from_start(state: DispositionState) -> str:
    return "settle" if not state["disposition"].requires_return else "receive"


def _route_after_inspect(state: DispositionState) -> str:
    # Grade D (destroyed) is written off; A/B/C are restocked/refurbished/liquidated.
    return "dispose" if state["disposition"].grade == "d" else "settle"


def _build_disposition_graph():
    graph = StateGraph(DispositionState)
    graph.add_node("start", _disposition_start)
    graph.add_node("receive", _disposition_receive)
    graph.add_node("inspect", _disposition_inspect)
    graph.add_node("settle", _disposition_settle)
    graph.add_node("dispose", _disposition_dispose)
    graph.add_edge(START, "start")
    graph.add_conditional_edges("start", _route_from_start, {"receive": "receive", "settle": "settle"})
    graph.add_edge("receive", "inspect")
    graph.add_conditional_edges("inspect", _route_after_inspect, {"settle": "settle", "dispose": "dispose"})
    graph.add_edge("settle", END)
    graph.add_edge("dispose", END)
    return graph.compile()


def run_disposition_lifecycle(
    *,
    decision,
    case_id: str,
    order_id: str,
    sku: str,
    received_at,
    sla_hours: int = 48,
) -> dict:
    """Run the disposition lifecycle graph; returns {disposition, route}."""
    return _DISPOSITION_GRAPH.invoke(
        {
            "decision": decision,
            "case_id": case_id,
            "order_id": order_id,
            "sku": sku,
            "received_at": received_at,
            "sla_hours": sla_hours,
        }
    )


_CASE_GRAPH = _build_case_graph()
_DISPOSITION_GRAPH = _build_disposition_graph()


def get_case_graph():
    """Expose the compiled case graph (for inspection/visualization)."""
    return _CASE_GRAPH


def get_disposition_graph():
    """Expose the compiled disposition graph (for inspection/visualization)."""
    return _DISPOSITION_GRAPH
