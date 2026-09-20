"""Shared persisted Action operations used by HTTP and Agent tools."""

from .actions import (
    ActionConflict,
    ActionRequest,
    create_refund_action,
    create_resend_action,
    execute_approved_action,
    execute_approved_resend,
)
from .intake import evaluate_intake, extract_intake, normalize_issue_type
from .mock_data import ORDERS, resolve_operator_refund_limit
from .repository import AfterSalesRepository
from .schemas import DecisionResult


async def create_intake_case(
    *,
    repo: AfterSalesRepository,
    user_id: str,
    user_role: str | None,
    complaint_text: str,
    thread_id: str | None = None,
    order_id: str | None = None,
    issue_type: str | None = None,
    customer_expectation: str | None = None,
    run_id: str | None = None,
    trace_id: str | None = None,
    structured_source: str = "trusted_ui",
) -> dict:
    extracted = extract_intake(complaint_text)
    if structured_source == "model":
        order_id = extracted["order_id"]
        issue_type = extracted["issue_type"]
        customer_expectation = extracted["customer_expectation"]
    else:
        order_id = order_id or extracted["order_id"]
        issue_type = normalize_issue_type(issue_type) or extracted["issue_type"]
        customer_expectation = customer_expectation if customer_expectation in {"refund", "replacement"} else extracted["customer_expectation"]
    evaluated = evaluate_intake(
        complaint_text=complaint_text,
        order_id=order_id,
        issue_type=issue_type,
        customer_expectation=customer_expectation,
        operator_refund_limit=resolve_operator_refund_limit(user_role),
    )
    evaluated["evidence"]["intake"]["structured_source"] = structured_source
    case = await repo.create_case(
        user_id=user_id,
        thread_id=thread_id,
        order_id=order_id,
        issue_type=issue_type,
        complaint_text=complaint_text,
        customer_expectation=customer_expectation,
        status=evaluated["status"],
        next_step=evaluated["next_step"],
        evidence=evaluated["evidence"],
        decision=evaluated["decision"],
        reply_draft=evaluated["reply_draft"],
    )
    await repo.append_event(
        case_id=case["id"],
        user_id=user_id,
        actor=user_id,
        event_type="intake_created",
        run_id=run_id,
        trace_id=trace_id,
        metadata={"thread_id": thread_id, "missing": evaluated["evidence"]["missing"]},
    )
    if evaluated["decision"]:
        await repo.append_event(
            case_id=case["id"],
            user_id=user_id,
            actor=user_id,
            event_type="decision_generated",
            run_id=run_id,
            trace_id=trace_id,
            metadata={"evidence_revision": evaluated["evidence"]["revision"]},
        )
    return case


async def continue_intake_case(
    *,
    repo: AfterSalesRepository,
    case: dict,
    actor_id: str,
    user_role: str | None,
    note: str,
    refund_executor,
    complaint_text: str | None = None,
    order_id: str | None = None,
    issue_type: str | None = None,
    customer_expectation: str | None = None,
    visual_evidence_confirmed: bool | None = None,
    damage_level: str | None = None,
    serial_matches: bool | None = None,
    run_id: str | None = None,
    trace_id: str | None = None,
    clear_fields: set[str] | None = None,
) -> dict:
    clear_fields = clear_fields or set()
    intake = case["evidence_json"].get("intake", {})
    complaint_text = complaint_text or case["complaint_text"]
    order_id = None if "order_id" in clear_fields else order_id or case["order_id"]
    issue_type = None if "issue_type" in clear_fields else issue_type or case["issue_type"]
    customer_expectation = None if "customer_expectation" in clear_fields else customer_expectation or case["customer_expectation"]
    confirmed = visual_evidence_confirmed if visual_evidence_confirmed is not None else bool(intake.get("visual_evidence_confirmed"))
    damage_level = damage_level or intake.get("damage_level")
    serial_matches = serial_matches if serial_matches is not None else intake.get("serial_matches")
    evaluated = evaluate_intake(
        complaint_text=complaint_text,
        order_id=order_id,
        issue_type=issue_type,
        customer_expectation=customer_expectation,
        operator_refund_limit=resolve_operator_refund_limit(user_role),
        visual_evidence_confirmed=confirmed,
        damage_level=damage_level,
        serial_matches=serial_matches,
        revision=int(case["evidence_json"].get("revision", 1)) + 1,
    )
    invalidated = await repo.invalidate_active_actions(case["id"], user_id=actor_id, reason="案件信息已更新，原方案失效")
    for action in invalidated:
        if action.action_type == "refund" and action.reserved:
            refund_executor.release(order_id=case["order_id"], amount=action.payload["amount"])
            await repo.save_action(
                action.model_copy(update={"reserved": False, "version": action.version + 1}),
                user_id=actor_id,
                expected_version=action.version,
            )
    case = await repo.update_case(
        case["id"],
        user_id=actor_id,
        expected_version=case["version"],
        values={
            "complaint_text": complaint_text,
            "order_id": order_id,
            "issue_type": issue_type,
            "customer_expectation": customer_expectation,
            "status": evaluated["status"],
            "next_step": evaluated["next_step"],
            "evidence_json": evaluated["evidence"],
            "decision_json": evaluated["decision"],
            "reply_draft": evaluated["reply_draft"],
        },
    )
    await repo.append_event(
        case_id=case["id"],
        user_id=actor_id,
        actor=actor_id,
        event_type="evidence_supplemented",
        run_id=run_id,
        trace_id=trace_id,
        metadata={"note": note, "evidence_revision": evaluated["evidence"]["revision"]},
    )
    await repo.append_event(
        case_id=case["id"],
        user_id=actor_id,
        actor=actor_id,
        event_type="case_reassessed",
        run_id=run_id,
        trace_id=trace_id,
        metadata={"status": evaluated["status"], "invalidated_actions": [action.id for action in invalidated]},
    )
    return case


async def create_case_action(*, repo: AfterSalesRepository, case: dict, actor_id: str, refund_executor) -> ActionRequest:
    if case["status"] != "decided" or not case.get("decision_json"):
        raise ActionConflict("case is not ready for an action")
    reverse = case["decision_json"].get("reverse") if case["decision_json"].get("route") == "reverse" else None
    if reverse is not None:
        if reverse.get("action") != "resend_without_return":
            raise ValueError("this reverse plan requires manual fulfillment")
        order = ORDERS.get(case["order_id"])
        if order is None:
            raise ValueError("order is unavailable")
        action = create_resend_action(
            case_id=case["id"],
            order_id=case["order_id"],
            sku=order["sku"],
            requested_by=actor_id,
            estimated_cost=reverse["estimated_resolution_cost"],
        )
    else:
        action = create_refund_action(
            case_id=case["id"],
            order_id=case["order_id"],
            requested_by=actor_id,
            decision=DecisionResult.model_validate(case["decision_json"]),
        )
    if action.action_type == "refund" and action.status == "approved":
        if not refund_executor.reserve(order_id=case["order_id"], amount=action.payload["amount"]):
            raise ActionConflict("insufficient available balance to fund this refund")
        action = action.model_copy(update={"reserved": True})
    try:
        saved = await repo.create_action(action, user_id=actor_id)
    except Exception:
        if action.action_type == "refund" and action.reserved:
            refund_executor.release(order_id=case["order_id"], amount=action.payload["amount"])
        raise
    await repo.update_case(
        case["id"],
        user_id=actor_id,
        expected_version=case["version"],
        values={
            "status": saved.status.value,
            "next_step": "supervisor_review" if saved.status == "pending_approval" else "execute_action",
        },
    )
    return saved


async def execute_case_action(
    *,
    repo: AfterSalesRepository,
    action: ActionRequest,
    case: dict,
    payload: dict,
    expected_version: int,
    refund_executor,
    reverse_executor,
) -> ActionRequest:
    if action.action_type == "resend":
        updated = execute_approved_resend(action, payload=payload, expected_version=expected_version, executor=reverse_executor)
    else:
        updated = execute_approved_action(
            action,
            payload=payload,
            expected_version=expected_version,
            current_refundable_balance=refund_executor.reserved_of(case["order_id"]),
            executor=refund_executor,
        ).model_copy(update={"reserved": False})
    saved = await repo.save_action(updated, user_id=None, expected_version=expected_version)
    await repo.update_case(
        case["id"],
        user_id=case["user_id"],
        expected_version=case["version"],
        values={
            "status": "completed" if action.action_type == "resend" else "execution_processing",
            "next_step": "done" if action.action_type == "resend" else "await_payment_confirmation",
            "reply_draft": (f"补发已安排，出库凭证：{saved.external_transaction_id}。" if action.action_type == "resend" else f"退款已提交至原支付渠道，当前处理中，凭证：{saved.external_transaction_id}。"),
        },
    )
    return saved
