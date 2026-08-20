"""LangChain tools exposing the AfterFlow demo business capabilities."""

import json
from typing import Any

from langchain.tools import tool

from deerflow.persistence.engine import get_session_factory
from deerflow.runtime.user_context import get_current_user
from deerflow.tools.types import Runtime
from deerflow.trace_context import get_current_trace_id

from .actions import create_refund_action, execute_approved_action, get_mock_refund_executor
from .mock_data import CUSTOMER_RISK, INVENTORY, LOGISTICS, OPERATIONS_METRICS, ORDERS, PAYMENTS, POLICIES, REVERSE_COSTS, resolve_operator_refund_limit
from .repository import AfterSalesRepository, ConcurrentActionError
from .reverse import CustomerPreference, ReverseInput, VisualEvidence, decide_reverse_fulfillment
from .risk_ops import MetricBucket, detect_after_sales_anomalies
from .schemas import DecisionResult, IssueType
from .workflow import run_case_evaluation


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _lookup(records: dict[str, dict], key: str, error: str, field: str) -> str:
    record = records.get(key)
    return _json(record if record is not None else {"error": error, field: key})


@tool("get_after_sales_order")
def get_order_context_tool(order_id: str) -> str:
    """Read the paid amount, customer, region, and status for an order."""
    return _lookup(ORDERS, order_id, "ORDER_NOT_FOUND", "order_id")


@tool("get_after_sales_payment")
def get_payment_context_tool(order_id: str) -> str:
    """Read the remaining refundable payment balance for an order."""
    return _lookup(PAYMENTS, order_id, "PAYMENT_NOT_FOUND", "order_id")


@tool("get_logistics_evidence")
def get_logistics_evidence_tool(order_id: str) -> str:
    """Read carrier status and proof-of-delivery evidence for an order."""
    return _lookup(LOGISTICS, order_id, "LOGISTICS_NOT_FOUND", "order_id")


@tool("get_customer_refund_risk")
def get_customer_risk_context_tool(customer_id: str) -> str:
    """Read recent refund and not-received claim counts for a customer."""
    return _lookup(CUSTOMER_RISK, customer_id, "CUSTOMER_NOT_FOUND", "customer_id")


@tool("get_after_sales_policy")
def get_policy_snapshot_tool(region: str = "CN") -> str:
    """Read the versioned after-sales policy for a region."""
    return _lookup(POLICIES, region, "POLICY_NOT_FOUND", "region")


@tool("get_replacement_inventory")
def get_replacement_inventory_tool(order_id: str) -> str:
    """Read replacement inventory and unit cost for the order SKU."""
    order = ORDERS.get(order_id)
    if order is None:
        return _json({"error": "ORDER_NOT_FOUND", "order_id": order_id})
    return _lookup(INVENTORY, order["sku"], "INVENTORY_NOT_FOUND", "sku")


@tool("get_reverse_fulfillment_costs")
def get_reverse_fulfillment_costs_tool(order_id: str) -> str:
    """Read return shipping, handling, recovery, and replacement shipping costs."""
    order = ORDERS.get(order_id)
    if order is None:
        return _json({"error": "ORDER_NOT_FOUND", "order_id": order_id})
    return _lookup(REVERSE_COSTS, order["sku"], "REVERSE_COST_NOT_FOUND", "sku")


@tool("evaluate_reverse_fulfillment")
def evaluate_reverse_fulfillment_tool(
    order_id: str,
    issue_type: str,
    customer_preference: str,
    damage_level: str,
    human_confirmed: bool,
    serial_matches: bool | None = None,
) -> str:
    """Choose return, replacement, or refund-without-return using deterministic economics."""
    order = ORDERS.get(order_id)
    payment = PAYMENTS.get(order_id)
    if order is None or payment is None:
        return _json({"error": "ORDER_OR_PAYMENT_NOT_FOUND", "order_id": order_id})
    inventory = INVENTORY.get(order["sku"])
    costs = REVERSE_COSTS.get(order["sku"])
    if inventory is None or costs is None:
        return _json({"error": "REVERSE_CONTEXT_NOT_FOUND", "sku": order["sku"]})
    case = ReverseInput(
        issue_type=IssueType(issue_type),
        item_value=order["item_paid"],
        refund_amount=min(order["item_paid"], payment["refundable_balance"]),
        return_shipping_cost=costs["return_shipping_cost"],
        handling_cost=costs["handling_cost"],
        expected_recovery_value=costs["expected_recovery_value"],
        replacement_unit_cost=inventory["replacement_unit_cost"],
        replacement_shipping_cost=costs["replacement_shipping_cost"],
        replacement_inventory=inventory["available"],
        customer_preference=CustomerPreference(customer_preference),
        visual_evidence=VisualEvidence(
            damage_level=damage_level,
            serial_matches=serial_matches,
            human_confirmed=human_confirmed,
        ),
    )
    return _json(decide_reverse_fulfillment(case).model_dump(mode="json"))


@tool("scan_after_sales_operations")
def scan_after_sales_operations_tool(
    minimum_orders: int = 50,
    minimum_issue_rate: float = 0.05,
    loss_threshold: int = 500_000,
) -> str:
    """Detect SKU, carrier, and warehouse issue-rate or loss spikes against the previous period."""
    alerts = detect_after_sales_anomalies(
        [MetricBucket(**item) for item in OPERATIONS_METRICS],
        minimum_orders=minimum_orders,
        minimum_issue_rate=minimum_issue_rate,
        loss_threshold=loss_threshold,
    )
    return _json({"window": "current_30d_vs_previous_30d", "alerts": [alert.model_dump() for alert in alerts]})


@tool("evaluate_after_sales_case")
def evaluate_after_sales_case_tool(
    order_id: str,
    issue_type: str,
    visual_evidence_confirmed: bool = False,
) -> str:
    """Calculate eligibility, refund amount, risk, and approval using deterministic rules.

    The operator refund limit is resolved server-side from the authenticated
    user's role — it is NOT a tool argument, so the LLM cannot influence the
    approval threshold by passing a large value.
    """
    user = get_current_user()
    system_role = getattr(user, "system_role", None)
    result = evaluate_mock_case(
        order_id=order_id,
        issue_type=issue_type,
        operator_refund_limit=resolve_operator_refund_limit(system_role),
        visual_evidence_confirmed=visual_evidence_confirmed,
    )
    if isinstance(result, dict):
        return _json(result)
    decision, _ = result
    payload = decision.model_dump(mode="json")
    payload["decision_source"] = "deterministic_policy_engine"
    return _json(payload)


def evaluate_mock_case(
    *,
    order_id: str,
    issue_type: str,
    operator_refund_limit: int,
    visual_evidence_confirmed: bool = False,
):
    """Build and evaluate a local demo case via the LangGraph case-evaluation workflow."""
    state = run_case_evaluation(
        order_id=order_id,
        issue_type=issue_type,
        operator_refund_limit=operator_refund_limit,
        visual_evidence_confirmed=visual_evidence_confirmed,
    )
    if state.get("error"):
        return state["error"]
    return state["decision"], state["evidence"]


def _runtime_context(runtime: Runtime) -> dict:
    return runtime.context if isinstance(runtime.context, dict) else {}


def _repository() -> AfterSalesRepository:
    session_factory = get_session_factory()
    if session_factory is None:
        raise RuntimeError("AfterFlow persistence is unavailable")
    return AfterSalesRepository(session_factory)


@tool("create_after_sales_action")
async def create_after_sales_action_tool(runtime: Runtime, case_id: str) -> str:
    """Create a persisted refund Action Request from an existing deterministic case decision."""
    context = _runtime_context(runtime)
    if context.get("is_subagent"):
        return _json({"error": "SUBAGENT_ACTION_FORBIDDEN"})
    user = get_current_user()
    user_id = str(user.id) if user is not None else str(context.get("user_id") or "")
    if not user_id:
        return _json({"error": "AUTHENTICATION_REQUIRED"})
    repo = _repository()
    case = await repo.get_case(case_id, user_id=user_id)
    if case is None:
        return _json({"error": "CASE_NOT_FOUND", "case_id": case_id})
    try:
        action = create_refund_action(
            case_id=case_id,
            order_id=case["order_id"],
            requested_by=user_id,
            decision=DecisionResult.model_validate(case["decision_json"]),
        )
        action = await repo.create_action(action, user_id=user_id)
    except (ValueError, ConcurrentActionError) as error:
        return _json({"error": "ACTION_NOT_CREATED", "detail": str(error)})
    if action.status == "approved":
        # Auto-approved actions freeze the funds immediately (authorize-capture).
        if not get_mock_refund_executor().reserve(order_id=case["order_id"], amount=action.payload["amount"]):
            return _json({"error": "INSUFFICIENT_BALANCE", "detail": "cannot fund auto-approved refund"})
    await repo.append_event(
        case_id=case_id,
        user_id=user_id,
        actor=user_id,
        event_type="approval_requested" if action.status == "pending_approval" else "action_approved",
        run_id=context.get("run_id"),
        trace_id=context.get("trace_id") or get_current_trace_id(),
        metadata={"action_id": action.id},
    )
    return _json(action.model_dump(mode="json"))


@tool("execute_approved_action")
async def execute_approved_action_tool(
    runtime: Runtime,
    action_id: str,
    expected_version: int,
    payload: dict,
) -> str:
    """Execute an approved refund after role, hash, version, expiry, balance, and idempotency checks."""
    context = _runtime_context(runtime)
    if context.get("is_subagent"):
        return _json({"error": "SUBAGENT_ACTION_FORBIDDEN"})
    user = get_current_user()
    role = getattr(user, "system_role", None) or context.get("user_role")
    actor_id = str(user.id) if user is not None else str(context.get("user_id") or "")
    if role != "admin" or not actor_id:
        return _json({"error": "SUPERVISOR_REQUIRED"})
    repo = _repository()
    action = await repo.get_action(action_id, user_id=None)
    if action is None:
        return _json({"error": "ACTION_NOT_FOUND", "action_id": action_id})
    case = await repo.get_case(action.case_id, user_id=None)
    payment = PAYMENTS.get(case["order_id"]) if case else None
    if case is None or payment is None:
        return _json({"error": "PAYMENT_STATE_UNAVAILABLE"})
    executor = get_mock_refund_executor()
    try:
        updated = execute_approved_action(
            action,
            payload=payload,
            expected_version=expected_version,
            # Capture from the reservation frozen at approval time.
            current_refundable_balance=executor.reserved_of(case["order_id"]),
            executor=executor,
        )
        updated = await repo.save_action(updated, user_id=None, expected_version=expected_version)
    except (ValueError, PermissionError, ConcurrentActionError) as error:
        return _json({"error": "ACTION_NOT_EXECUTED", "detail": str(error)})
    await repo.append_event(
        case_id=case["id"],
        user_id=case["user_id"],
        actor=actor_id,
        event_type="execution_succeeded",
        run_id=context.get("run_id"),
        trace_id=context.get("trace_id") or get_current_trace_id(),
        metadata={"action_id": action_id, "transaction_id": updated.external_transaction_id},
    )
    return _json(updated.model_dump(mode="json"))
