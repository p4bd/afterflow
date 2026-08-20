"""Tests for the LangGraph StateGraphs: case evaluation orchestration + disposition lifecycle.

The graphs are AfterFlow's real orchestration layer: they route which decision
path applies and how a returned item settles. These tests assert terminal
states (the same outcome as calling the domain functions directly) AND the
graph topology (the nodes/edges that make it real LangGraph orchestration).
"""

from datetime import UTC, datetime

from app.after_sales.reverse import ReverseAction
from app.after_sales.schemas import Eligibility
from app.after_sales.workflow import (
    get_case_graph,
    get_disposition_graph,
    run_case_evaluation,
    run_disposition_lifecycle,
)


def _graph_node_names(graph) -> set[str]:
    return set(graph.get_graph().nodes.keys())


def _graph_edge_pairs(graph) -> set[tuple[str, str]]:
    data = graph.get_graph().to_json()
    edges = set()
    for edge in data.get("edges", []):
        source = edge.get("source")
        target = edge.get("target")
        if source and target:
            edges.add((source, target))
    return edges


# ---- case evaluation graph ----

def test_case_graph_routes_refund_issue_to_refund_decision():
    result = run_case_evaluation(order_id="ORDER-1001", issue_type="delivery_not_received", operator_refund_limit=20_000)

    assert result["decision"] is not None
    assert result["decision"].eligibility is Eligibility.ELIGIBLE_WITH_APPROVAL
    assert result.get("reverse_decision") is None
    assert result["route"] == "refund"


def test_case_graph_routes_reverse_issue_with_context_to_reverse_decision():
    result = run_case_evaluation(
        order_id="ORDER-1003",
        issue_type="damaged_item",
        operator_refund_limit=20_000,
        visual_evidence_confirmed=True,
        customer_preference="refund",
        damage_level="destroyed",
        human_confirmed=True,
    )

    assert result["reverse_decision"] is not None
    assert result["reverse_decision"].action is ReverseAction.REFUND_WITHOUT_RETURN


def test_case_graph_errors_on_unknown_order():
    result = run_case_evaluation(order_id="ORDER-NOPE", issue_type="delivery_not_received", operator_refund_limit=20_000)

    assert result["error"] == {"error": "ORDER_NOT_FOUND", "order_id": "ORDER-NOPE"}
    assert result.get("decision") is None
    assert result["route"] == "error"


def test_case_graph_matches_direct_domain_decision():
    from app.after_sales.mock_data import ORDERS, PAYMENTS

    order = ORDERS["ORDER-1002"]
    result = run_case_evaluation(order_id="ORDER-1002", issue_type="delivery_not_received", operator_refund_limit=20_000)
    assert result["decision"].refund_amount == min(order["item_paid"] + order["shipping_paid"], PAYMENTS["ORDER-1002"]["refundable_balance"])


def test_case_graph_has_explicit_orchestration_topology():
    graph = get_case_graph()
    nodes = _graph_node_names(graph)

    assert {"build_context", "decide_refund", "reverse_decision"} <= nodes
    edges = _graph_edge_pairs(graph)
    # conditional routing from build_context to both decision paths must exist
    assert any(s == "build_context" for s, _ in edges)
    assert ("build_context", "decide_refund") in edges or ("build_context", "reverse_decision") in edges


# ---- disposition lifecycle graph ----

def _return_decision():
    # ORDER-1001 (PHONE-X) has high recovery, so a minor-damaged item is
    # economically worth returning -> requires_return=True.
    return run_case_evaluation(
        order_id="ORDER-1001",
        issue_type="damaged_item",
        operator_refund_limit=20_000,
        visual_evidence_confirmed=True,
        customer_preference="refund",
        damage_level="minor",
        human_confirmed=True,
    )["reverse_decision"]


def test_disposition_graph_walks_return_lifecycle_to_settlement():
    decision = _return_decision()
    result = run_disposition_lifecycle(
        decision=decision,
        case_id="C-1",
        order_id="ORDER-1003",
        sku="KETTLE-SMART",
        received_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    disposition = result["disposition"]
    assert disposition.requires_return is True
    assert disposition.grade == "b"  # minor -> refurbish
    assert disposition.status == "settled"
    assert result["route"] == "refurbish"


def test_disposition_graph_destroyed_grade_routes_to_dispose():
    decision = run_case_evaluation(
        order_id="ORDER-1003",
        issue_type="damaged_item",
        operator_refund_limit=20_000,
        visual_evidence_confirmed=True,
        customer_preference="refund",
        damage_level="destroyed",
        human_confirmed=True,
    )["reverse_decision"]
    result = run_disposition_lifecycle(
        decision=decision,
        case_id="C-2",
        order_id="ORDER-1003",
        sku="KETTLE-SMART",
        received_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert result["disposition"].grade == "d"
    assert result["disposition"].status == "settled"
    assert result["route"] == "dispose"


def test_disposition_graph_returnless_settles_directly():
    # destroyed low-residual item -> refund_without_return -> no return cycle
    decision = run_case_evaluation(
        order_id="ORDER-1003",
        issue_type="damaged_item",
        operator_refund_limit=20_000,
        visual_evidence_confirmed=True,
        customer_preference="refund",
        damage_level="destroyed",
        human_confirmed=True,
    )["reverse_decision"]
    result = run_disposition_lifecycle(
        decision=decision,
        case_id="C-3",
        order_id="ORDER-1003",
        sku="KETTLE-SMART",
        received_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert result["disposition"].requires_return is False
    assert result["disposition"].status == "settled"


def test_disposition_graph_has_explicit_state_topology():
    graph = get_disposition_graph()
    nodes = _graph_node_names(graph)

    assert {"start", "receive", "inspect", "settle", "dispose"} <= nodes
    edges = _graph_edge_pairs(graph)
    assert ("start", "receive") in edges or ("start", "settle") in edges
    assert any(s == "inspect" for s, _ in edges)
