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
    reject_action,
)
from app.after_sales.balance_check import check_balance_before_approve
from app.after_sales.idempotency import IdempotencyConflict, apply_idempotency
from app.after_sales.metrics import inc_metrics
from app.after_sales.mock_data import resolve_operator_refund_limit
from app.after_sales.operations import (
    continue_intake_case,
    create_case_action,
    create_intake_case,
    execute_case_action,
)
from app.after_sales.repository import AfterSalesRepository, ConcurrentActionError, release_expired_reservations
from app.after_sales.reverse_execution import get_mock_reverse_executor
from app.after_sales.schemas import IssueType
from app.after_sales.tools import evaluate_mock_case
from deerflow.trace_context import get_current_trace_id

router = APIRouter(prefix="/api/after-sales", tags=["after-sales"])


class CaseCreateRequest(BaseModel):
    order_id: str = Field(min_length=1)
    issue_type: IssueType
    visual_evidence_confirmed: bool = False
    thread_id: str | None = None


class CaseIntakeRequest(BaseModel):
    complaint_text: str = Field(min_length=1, max_length=10_000)
    thread_id: str | None = None
    order_id: str | None = None
    issue_type: IssueType | None = None
    customer_expectation: Literal["refund", "replacement"] | None = None


class CaseSupplementRequest(BaseModel):
    complaint_text: str | None = Field(default=None, min_length=1, max_length=10_000)
    order_id: str | None = None
    issue_type: IssueType | None = None
    customer_expectation: Literal["refund", "replacement"] | None = None
    visual_evidence_confirmed: bool | None = None
    damage_level: Literal["none", "minor", "major", "destroyed"] | None = None
    serial_matches: bool | None = None
    note: str = Field(min_length=1, max_length=2_000)


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


def _idem_store(request: Request):
    return getattr(request.app.state, "after_sales_idempotency_store", None)


async def _idem_replay_or_run(request: Request, *, endpoint: str, body: dict, run, response_status: int = 200):
    """Apply Idempotency-Key before calling run(); replay cache if hit.

    Returns the FastAPI response. A reused Idempotency-Key with a different
    body becomes HTTP 422, never a silent overwrite or a double side effect.
    """
    store = _idem_store(request)
    key = request.headers.get("Idempotency-Key")
    actor_id, role = _actor(request)
    if store is None or not key:
        # No key supplied (or no store wired) → treat as miss so the
        # idempotency counter still reflects "this request had no
        # Idempotency-Key header". We deliberately do NOT increment here:
        # a counter that fires on every endpoint would dwarf the actual
        # contract signal. Hit/miss/conflict below cover the interesting
        # branches.
        return await run()
    scoped_endpoint = f"{actor_id}:{role}:{endpoint}"
    async with store.lock_for(key=key, endpoint=scoped_endpoint):
        try:
            outcome = await apply_idempotency(store, key=key, endpoint=scoped_endpoint, body=body)
        except IdempotencyConflict as conflict:
            inc_metrics("idempotency_total", result="conflict")
            raise HTTPException(status_code=422, detail=str(conflict)) from conflict
        if outcome.replay:
            inc_metrics("idempotency_total", result="hit")
            return outcome.cached_response
        inc_metrics("idempotency_total", result="miss")
        response = await run()
        await store.store(key=key, endpoint=scoped_endpoint, body=body, response=response, status_code=response_status)
        return response


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


async def _aggregate_case(repo: AfterSalesRepository, case: dict, *, user_id: str) -> dict:
    actions = await repo.list_case_actions(case["id"], user_id=user_id)
    events = await repo.list_events(case["id"], user_id=user_id)
    return {**case, "actions": [action.model_dump(mode="json") for action in actions], "events": events}


@router.get("/cases")
async def list_cases(request: Request) -> list[dict]:
    actor_id, _ = _actor(request)
    return await _repo(request).list_cases(user_id=actor_id)


@router.post("/cases/intake", status_code=status.HTTP_201_CREATED)
async def intake_case(body: CaseIntakeRequest, request: Request) -> dict:
    async def _run() -> dict:
        actor_id, role = _actor(request)
        return await create_intake_case(
            repo=_repo(request),
            user_id=actor_id,
            user_role=role,
            thread_id=body.thread_id,
            complaint_text=body.complaint_text,
            order_id=body.order_id,
            issue_type=body.issue_type.value if body.issue_type else None,
            customer_expectation=body.customer_expectation,
            trace_id=get_current_trace_id(),
        )

    return await _idem_replay_or_run(
        request,
        endpoint="/api/after-sales/cases/intake",
        body=body.model_dump(),
        run=_run,
        response_status=status.HTTP_201_CREATED,
    )


@router.post("/cases", status_code=status.HTTP_201_CREATED)
async def create_case(body: CaseCreateRequest, request: Request) -> dict:
    async def _run() -> dict:
        actor_id, role = _actor(request)
        # The operator refund limit is server-authoritative: derived from the
        # authenticated role, never from the request body or the LLM.
        evaluated = evaluate_mock_case(
            order_id=body.order_id,
            issue_type=body.issue_type.value,
            operator_refund_limit=resolve_operator_refund_limit(role),
            visual_evidence_confirmed=body.visual_evidence_confirmed,
        )
        if isinstance(evaluated, dict):
            raise HTTPException(status_code=404, detail=evaluated)
        decision, evidence = evaluated
        # Observability counter: every deterministic decision shows up here,
        # split by eligibility. The split makes the auto-vs-supervisor ratio
        # trivially computable in Grafana without parsing the audit chain.
        inc_metrics("decisions_total", outcome=decision.eligibility.value)
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

    return await _idem_replay_or_run(
        request,
        endpoint="/api/after-sales/cases",
        body=body.model_dump(),
        run=_run,
        response_status=status.HTTP_201_CREATED,
    )


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request) -> dict:
    actor_id, role = _actor(request)
    repo = _repo(request)
    case = await repo.get_case(case_id, user_id=None if role == "admin" else actor_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return await _aggregate_case(repo, case, user_id=case["user_id"])


@router.post("/cases/{case_id}/supplements")
async def supplement_case(case_id: str, body: CaseSupplementRequest, request: Request) -> dict:
    async def _run() -> dict:
        actor_id, role = _actor(request)
        repo = _repo(request)
        case = await repo.get_case(case_id, user_id=actor_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")
        case = await continue_intake_case(
            repo=repo,
            case=case,
            actor_id=actor_id,
            user_role=role,
            note=body.note,
            refund_executor=request.app.state.after_sales_refund_executor,
            complaint_text=body.complaint_text,
            order_id=body.order_id,
            issue_type=body.issue_type.value if body.issue_type else None,
            customer_expectation=body.customer_expectation,
            visual_evidence_confirmed=body.visual_evidence_confirmed,
            damage_level=body.damage_level,
            serial_matches=body.serial_matches,
            trace_id=get_current_trace_id(),
            clear_fields={name for name in ("order_id", "issue_type", "customer_expectation") if name in body.model_fields_set and getattr(body, name) is None},
        )
        return await _aggregate_case(repo, case, user_id=actor_id)

    return await _idem_replay_or_run(
        request,
        endpoint=f"/api/after-sales/cases/{case_id}/supplements",
        body={"case_id": case_id, **body.model_dump()},
        run=_run,
    )


@router.post("/cases/{case_id}/actions", status_code=status.HTTP_201_CREATED)
async def create_action(case_id: str, request: Request) -> dict:
    async def _run() -> dict:
        actor_id, _ = _actor(request)
        repo = _repo(request)
        case = await repo.get_case(case_id, user_id=actor_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")
        try:
            action = await create_case_action(
                repo=repo,
                case=case,
                actor_id=actor_id,
                refund_executor=request.app.state.after_sales_refund_executor,
            )
        except (ValueError, ConcurrentActionError) as error:
            _raise_action_error(error)
        inc_metrics("actions_created_total", status=action.status.value)
        await repo.append_event(
            case_id=case_id,
            user_id=actor_id,
            actor=actor_id,
            event_type="approval_requested" if action.status == "pending_approval" else "action_approved",
            trace_id=get_current_trace_id(),
            metadata={"action_id": action.id},
        )
        return action.model_dump(mode="json")

    return await _idem_replay_or_run(
        request,
        endpoint=f"/api/after-sales/cases/{case_id}/actions",
        body={"case_id": case_id},
        run=_run,
        response_status=status.HTTP_201_CREATED,
    )


async def _admin_action(request: Request, action_id: str) -> tuple[str, str, AfterSalesRepository, ActionRequest, dict]:
    actor_id, role = _actor(request)
    if role != "admin":
        raise HTTPException(status_code=403, detail="After-sales supervisor role required")
    repo = _repo(request)
    action = await repo.get_action(action_id, user_id=None)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")
    case = await repo.get_case(action.case_id, user_id=None)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return actor_id, role, repo, action, case


@router.post("/actions/{action_id}/approve")
async def approve(action_id: str, body: ApprovalRequest, request: Request) -> dict:
    async def _run() -> dict:
        actor_id, role, repo, action, case = await _admin_action(request, action_id)
        executor: MockRefundExecutor = request.app.state.after_sales_refund_executor
        try:
            # P0-3: re-check balance before approval. The execute-time check
            # is too late — fail fast with a clear 409 instead of approving
            # an action we already know will fail at execute.
            if action.action_type == "refund" and not action.reserved:
                check_balance_before_approve(
                    executor,
                    order_id=action.payload.get("order_id", ""),
                    amount=action.payload["amount"],
                )
            updated = approve_action(
                action,
                approver_id=actor_id,
                approver_roles={role},
                expected_version=body.expected_version,
                comment=body.comment,
            )
            # Authorize-then-capture: freeze the funds BEFORE persisting the
            # approval, so we never end up APPROVED-but-unfunded. Roll back the
            # reservation if the DB save fails.
            if action.action_type == "refund" and not action.reserved:
                if not executor.reserve(order_id=case["order_id"], amount=updated.payload["amount"]):
                    raise HTTPException(status_code=409, detail="Insufficient available balance to fund this refund")
                updated = updated.model_copy(update={"reserved": True})
            try:
                updated = await repo.save_action(updated, user_id=None, expected_version=body.expected_version)
            except Exception:
                if action.action_type == "refund" and updated.reserved:
                    executor.release(order_id=case["order_id"], amount=updated.payload["amount"])
                raise
        except (ActionConflict, ActionForbidden, ConcurrentActionError, ValueError) as error:
            _raise_action_error(error)
        # Observability counter: a granted approval is the most-watched
        # AfterFlow signal — operators want the "approvals per minute" panel
        # at the top of the dashboard.
        inc_metrics("approvals_total", decision="granted")
        await repo.append_event(
            case_id=case["id"],
            user_id=case["user_id"],
            actor=actor_id,
            event_type="approval_granted",
            trace_id=get_current_trace_id(),
            metadata={"action_id": action_id},
        )
        await repo.update_case(
            case["id"],
            user_id=case["user_id"],
            expected_version=case["version"],
            values={"status": updated.status.value, "next_step": "execute_action" if updated.status == "approved" else "additional_approval"},
        )
        return updated.model_dump(mode="json")

    return await _idem_replay_or_run(
        request,
        endpoint=f"/api/after-sales/actions/{action_id}/approve",
        body={"action_id": action_id, **body.model_dump()},
        run=_run,
    )


@router.get("/actions")
async def list_actions(request: Request, action_status: str | None = None) -> list[dict]:
    _require_admin(request)
    repo = _repo(request)
    executor: MockRefundExecutor = request.app.state.after_sales_refund_executor
    # Release reservations on expired approvals. In production this runs as a
    # periodic reaper; on the approval-desk load it keeps the demo honest so
    # expired approvals don't leak the frozen funds.
    released = await release_expired_reservations(repo, executor)
    # Observability counter: each released reservation is one piece of
    # frozen money returned to the available pool. A persistent non-zero
    # rate is a signal that approvals are expiring before execution —
    # either the operator is slow or the TTL is too short.
    if released:
        for _ in range(released):
            inc_metrics("reservation_reaped_total")
    rows = await repo.list_actions_with_cases(status=action_status)
    return [{**action.model_dump(mode="json"), "case": case} for action, case in rows]


@router.post("/actions/{action_id}/reject")
async def reject(action_id: str, body: RejectionRequest, request: Request) -> dict:
    async def _run() -> dict:
        actor_id, role, repo, action, case = await _admin_action(request, action_id)
        try:
            updated = reject_action(
                action,
                approver_id=actor_id,
                approver_roles={role},
                expected_version=body.expected_version,
                comment=body.comment,
            )
            # A rejected action releases the reservation only if it had one.
            executor: MockRefundExecutor = request.app.state.after_sales_refund_executor
            if action.action_type == "refund" and action.reserved:
                executor.release(order_id=case["order_id"], amount=updated.payload["amount"])
                updated = updated.model_copy(update={"reserved": False})
            updated = await repo.save_action(updated, user_id=None, expected_version=body.expected_version)
        except (ActionConflict, ActionForbidden, ConcurrentActionError, ValueError) as error:
            _raise_action_error(error)
        # Observability counter: rejections matter for the same reason as
        # grants — they are the second leg of the "every action lands
        # somewhere" funnel.
        inc_metrics("approvals_total", decision="rejected")
        await repo.append_event(
            case_id=case["id"],
            user_id=case["user_id"],
            actor=actor_id,
            event_type="approval_rejected",
            trace_id=get_current_trace_id(),
            metadata={"action_id": action_id, "comment": body.comment},
        )
        await repo.update_case(
            case["id"],
            user_id=case["user_id"],
            expected_version=case["version"],
            values={"status": "rejected", "next_step": "operator_review", "reply_draft": "处理方案未获批准，客服将根据审批意见继续处理。"},
        )
        return updated.model_dump(mode="json")

    return await _idem_replay_or_run(
        request,
        endpoint=f"/api/after-sales/actions/{action_id}/reject",
        body={"action_id": action_id, **body.model_dump()},
        run=_run,
    )


@router.post("/actions/{action_id}/execute")
async def execute(action_id: str, body: ExecutionRequest, request: Request) -> dict:
    async def _run() -> dict:
        actor_id, _role, repo, action, case = await _admin_action(request, action_id)
        executor: MockRefundExecutor = request.app.state.after_sales_refund_executor
        try:
            updated = await execute_case_action(
                repo=repo,
                action=action,
                case=case,
                payload=body.payload,
                expected_version=body.expected_version,
                refund_executor=executor,
                reverse_executor=get_mock_reverse_executor(),
            )
        except (ActionConflict, ActionForbidden, ConcurrentActionError, ValueError) as error:
            _raise_action_error(error)
        # Observability counter: an executed action is the system actually
        # moving money. We only count the success path here; failures
        # surface as 4xx/5xx and are visible via existing access logs.
        inc_metrics("executions_total", outcome="succeeded")
        await repo.append_event(
            case_id=case["id"],
            user_id=case["user_id"],
            actor=actor_id,
            event_type="execution_succeeded",
            trace_id=get_current_trace_id(),
            metadata={"action_id": action_id, "transaction_id": updated.external_transaction_id},
        )
        result = updated.model_dump(mode="json")
        # Refund settlement metadata: funds go back to the original payment channel
        # and are reported as "processing" with an ETA (demo bank confirmation).
        if action.action_type == "refund":
            result["refund_status"] = "processing"
            result["refund_channel"] = executor.REFUND_CHANNEL
            result["refund_eta_hours"] = executor.REFUND_ETA_HOURS
        return result

    return await _idem_replay_or_run(
        request,
        endpoint=f"/api/after-sales/actions/{action_id}/execute",
        body={"action_id": action_id, **body.model_dump()},
        run=_run,
    )
