"""Authenticated AfterFlow case, approval, and mock-execution API."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.after_sales.actions import (
    ActionConflict,
    ActionForbidden,
    ActionRequest,
    MockRefundExecutor,
    approve_action,
    create_refund_action,
    execute_approved_action,
    reject_action,
)
from app.after_sales.mock_data import PAYMENTS
from app.after_sales.repository import AfterSalesRepository, ConcurrentActionError
from app.after_sales.schemas import DecisionResult, IssueType
from app.after_sales.tools import evaluate_mock_case
from deerflow.trace_context import get_current_trace_id

router = APIRouter(prefix="/api/after-sales", tags=["after-sales"])


class CaseCreateRequest(BaseModel):
    order_id: str = Field(min_length=1)
    issue_type: IssueType
    operator_refund_limit: int = Field(ge=0)
    visual_evidence_confirmed: bool = False
    thread_id: str | None = None


class ApprovalRequest(BaseModel):
    expected_version: int = Field(ge=1)
    comment: str | None = None


class RejectionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    comment: str = Field(min_length=1)


class ExecutionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    payload: dict


def _repo(request: Request) -> AfterSalesRepository:
    repo = getattr(request.app.state, "after_sales_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="AfterFlow persistence is unavailable")
    return repo


def _actor(request: Request) -> tuple[str, Literal["admin", "user"]]:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(user.id), user.system_role


def _require_admin(request: Request) -> str:
    actor_id, role = _actor(request)
    if role != "admin":
        raise HTTPException(status_code=403, detail="After-sales supervisor role required")
    return actor_id


def _raise_action_error(error: Exception) -> None:
    if isinstance(error, ActionForbidden):
        raise HTTPException(status_code=403, detail=str(error)) from error
    if isinstance(error, (ActionConflict, ConcurrentActionError)):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, ValueError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error


@router.post("/cases", status_code=status.HTTP_201_CREATED)
async def create_case(body: CaseCreateRequest, request: Request) -> dict:
    actor_id, _ = _actor(request)
    evaluated = evaluate_mock_case(
        order_id=body.order_id,
        issue_type=body.issue_type.value,
        operator_refund_limit=body.operator_refund_limit,
        visual_evidence_confirmed=body.visual_evidence_confirmed,
    )
    if isinstance(evaluated, dict):
        raise HTTPException(status_code=404, detail=evaluated)
    decision, evidence = evaluated
    repo = _repo(request)
    case = await repo.create_case(
        user_id=actor_id,
        thread_id=body.thread_id,
        order_id=body.order_id,
        issue_type=body.issue_type.value,
        evidence=evidence,
        decision=decision.model_dump(mode="json"),
    )
    await repo.append_event(
        case_id=case["id"],
        user_id=actor_id,
        actor=actor_id,
        event_type="decision_generated",
        trace_id=get_current_trace_id(),
        metadata={"policy_refs": decision.policy_refs},
    )
    return case


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request) -> dict:
    actor_id, _ = _actor(request)
    case = await _repo(request).get_case(case_id, user_id=actor_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.post("/cases/{case_id}/actions", status_code=status.HTTP_201_CREATED)
async def create_action(case_id: str, request: Request) -> dict:
    actor_id, _ = _actor(request)
    repo = _repo(request)
    case = await repo.get_case(case_id, user_id=actor_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        action = create_refund_action(
            case_id=case_id,
            order_id=case["order_id"],
            requested_by=actor_id,
            decision=DecisionResult.model_validate(case["decision_json"]),
        )
        action = await repo.create_action(action, user_id=actor_id)
    except (ValueError, ConcurrentActionError) as error:
        _raise_action_error(error)
    await repo.append_event(
        case_id=case_id,
        user_id=actor_id,
        actor=actor_id,
        event_type="approval_requested" if action.status == "pending_approval" else "action_approved",
        trace_id=get_current_trace_id(),
        metadata={"action_id": action.id},
    )
    return action.model_dump(mode="json")


async def _admin_action(request: Request, action_id: str) -> tuple[str, AfterSalesRepository, ActionRequest, dict]:
    actor_id = _require_admin(request)
    repo = _repo(request)
    action = await repo.get_action(action_id, user_id=None)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")
    case = await repo.get_case(action.case_id, user_id=None)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return actor_id, repo, action, case


@router.post("/actions/{action_id}/approve")
async def approve(action_id: str, body: ApprovalRequest, request: Request) -> dict:
    actor_id, repo, action, case = await _admin_action(request, action_id)
    try:
        updated = approve_action(
            action,
            approver_id=actor_id,
            approver_roles={"after_sales_supervisor"},
            expected_version=body.expected_version,
            comment=body.comment,
        )
        updated = await repo.save_action(updated, user_id=None, expected_version=body.expected_version)
    except (ActionConflict, ActionForbidden, ConcurrentActionError, ValueError) as error:
        _raise_action_error(error)
    await repo.append_event(
        case_id=case["id"],
        user_id=case["user_id"],
        actor=actor_id,
        event_type="approval_granted",
        trace_id=get_current_trace_id(),
        metadata={"action_id": action_id},
    )
    return updated.model_dump(mode="json")


@router.get("/actions")
async def list_actions(request: Request, action_status: str | None = None) -> list[dict]:
    _require_admin(request)
    repo = _repo(request)
    actions = await repo.list_actions(status=action_status)
    result = []
    # ponytail: queue is capped at 100; replace with one joined query if approval volume grows.
    for action in actions:
        case = await repo.get_case(action.case_id, user_id=None)
        result.append({**action.model_dump(mode="json"), "case": case})
    return result


@router.post("/actions/{action_id}/reject")
async def reject(action_id: str, body: RejectionRequest, request: Request) -> dict:
    actor_id, repo, action, case = await _admin_action(request, action_id)
    try:
        updated = reject_action(
            action,
            approver_id=actor_id,
            approver_roles={"after_sales_supervisor"},
            expected_version=body.expected_version,
            comment=body.comment,
        )
        updated = await repo.save_action(updated, user_id=None, expected_version=body.expected_version)
    except (ActionConflict, ActionForbidden, ConcurrentActionError, ValueError) as error:
        _raise_action_error(error)
    await repo.append_event(
        case_id=case["id"],
        user_id=case["user_id"],
        actor=actor_id,
        event_type="approval_rejected",
        trace_id=get_current_trace_id(),
        metadata={"action_id": action_id, "comment": body.comment},
    )
    return updated.model_dump(mode="json")


@router.post("/actions/{action_id}/execute")
async def execute(action_id: str, body: ExecutionRequest, request: Request) -> dict:
    actor_id, repo, action, case = await _admin_action(request, action_id)
    payment = PAYMENTS.get(case["order_id"])
    if payment is None:
        raise HTTPException(status_code=409, detail="Payment state is unavailable")
    executor: MockRefundExecutor = request.app.state.after_sales_refund_executor
    try:
        updated = execute_approved_action(
            action,
            payload=body.payload,
            expected_version=body.expected_version,
            current_refundable_balance=payment["refundable_balance"],
            executor=executor,
        )
        updated = await repo.save_action(updated, user_id=None, expected_version=body.expected_version)
    except (ActionConflict, ActionForbidden, ConcurrentActionError, ValueError) as error:
        _raise_action_error(error)
    await repo.append_event(
        case_id=case["id"],
        user_id=case["user_id"],
        actor=actor_id,
        event_type="execution_succeeded",
        trace_id=get_current_trace_id(),
        metadata={"action_id": action_id, "transaction_id": updated.external_transaction_id},
    )
    return updated.model_dump(mode="json")
