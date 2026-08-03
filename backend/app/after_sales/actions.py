"""Deterministic approval and idempotent refund execution boundary."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from .schemas import DecisionResult, Eligibility, ResolutionAction, RiskLevel


class ActionStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"


class ActionConflict(ValueError):
    """Action state, version, payload, balance, or expiry no longer matches."""


class ActionForbidden(PermissionError):
    """Authenticated actor lacks permission for the action."""


class ActionRequest(BaseModel):
    id: str
    case_id: str
    action_type: str = "refund"
    payload: dict
    payload_hash: str
    status: ActionStatus
    risk_level: RiskLevel
    requested_by: str
    required_role: str = "after_sales_supervisor"
    idempotency_key: str
    approved_by: str | None = None
    comment: str | None = None
    approved_at: datetime | None = None
    expires_at: datetime
    external_transaction_id: str | None = None
    version: int = Field(default=1, ge=1)


def payload_hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def create_refund_action(
    *,
    case_id: str,
    order_id: str,
    requested_by: str,
    decision: DecisionResult,
    now: datetime | None = None,
    ttl: timedelta = timedelta(hours=24),
) -> ActionRequest:
    if decision.eligibility not in {Eligibility.ELIGIBLE, Eligibility.ELIGIBLE_WITH_APPROVAL}:
        raise ValueError("decision is not eligible for an action")
    if decision.action is not ResolutionAction.REFUND_ORIGINAL_PAYMENT or decision.refund_amount <= 0:
        raise ValueError("decision does not contain an executable refund")
    now = now or datetime.now(UTC)
    payload = {"order_id": order_id, "amount": decision.refund_amount}
    auto_approved = not decision.approval_required and decision.risk_level is not RiskLevel.HIGH
    return ActionRequest(
        id=str(uuid.uuid4()),
        case_id=case_id,
        payload=payload,
        payload_hash=payload_hash(payload),
        status=ActionStatus.APPROVED if auto_approved else ActionStatus.PENDING_APPROVAL,
        risk_level=decision.risk_level,
        requested_by=requested_by,
        idempotency_key=str(uuid.uuid4()),
        approved_by="system-policy" if auto_approved else None,
        approved_at=now if auto_approved else None,
        expires_at=now + ttl,
    )


def approve_action(
    action: ActionRequest,
    *,
    approver_id: str,
    approver_roles: set[str],
    expected_version: int,
    now: datetime | None = None,
    comment: str | None = None,
) -> ActionRequest:
    now = now or datetime.now(UTC)
    _check_decision(action, approver_id, approver_roles, expected_version, now)
    return action.model_copy(
        update={
            "status": ActionStatus.APPROVED,
            "approved_by": approver_id,
            "approved_at": now,
            "comment": comment,
            "version": action.version + 1,
        }
    )


def reject_action(
    action: ActionRequest,
    *,
    approver_id: str,
    approver_roles: set[str],
    expected_version: int,
    comment: str,
    now: datetime | None = None,
) -> ActionRequest:
    if not comment.strip():
        raise ValueError("rejection comment is required")
    now = now or datetime.now(UTC)
    _check_decision(action, approver_id, approver_roles, expected_version, now)
    return action.model_copy(
        update={
            "status": ActionStatus.REJECTED,
            "approved_by": approver_id,
            "approved_at": now,
            "comment": comment.strip(),
            "version": action.version + 1,
        }
    )


def _check_decision(
    action: ActionRequest,
    approver_id: str,
    approver_roles: set[str],
    expected_version: int,
    now: datetime,
) -> None:
    if action.status is not ActionStatus.PENDING_APPROVAL:
        raise ActionConflict("action is not pending approval")
    if action.version != expected_version:
        raise ActionConflict("action version conflict")
    if now >= action.expires_at:
        raise ActionConflict("action expired")
    if action.required_role not in approver_roles:
        raise ActionForbidden("approver lacks required role")
    if action.risk_level is RiskLevel.HIGH and action.requested_by == approver_id:
        raise ActionForbidden("requester cannot approve own high-risk action")


class MockRefundExecutor:
    """In-memory payment adapter used only by the local demo and tests."""

    def __init__(self) -> None:
        self.transactions: dict[str, str] = {}

    def refund(self, *, order_id: str, amount: int, idempotency_key: str) -> str:
        if idempotency_key not in self.transactions:
            digest = hashlib.sha256(f"{order_id}:{amount}:{idempotency_key}".encode()).hexdigest()[:16]
            self.transactions[idempotency_key] = f"MOCK-REFUND-{digest}"
        return self.transactions[idempotency_key]


_mock_refund_executor = MockRefundExecutor()


def get_mock_refund_executor() -> MockRefundExecutor:
    return _mock_refund_executor


def execute_approved_action(
    action: ActionRequest,
    *,
    payload: dict,
    expected_version: int,
    current_refundable_balance: int,
    executor: MockRefundExecutor,
    now: datetime | None = None,
) -> ActionRequest:
    now = now or datetime.now(UTC)
    if action.status is not ActionStatus.APPROVED:
        raise ActionForbidden("action is not approved")
    if action.version != expected_version:
        raise ActionConflict("action version conflict")
    if now >= action.expires_at:
        raise ActionConflict("action expired")
    if payload_hash(action.payload) != action.payload_hash or payload_hash(payload) != action.payload_hash:
        raise ActionConflict("payload hash mismatch")
    if current_refundable_balance < action.payload["amount"]:
        raise ActionConflict("refundable balance changed")

    transaction_id = executor.refund(**action.payload, idempotency_key=action.idempotency_key)
    return action.model_copy(
        update={
            "status": ActionStatus.COMPLETED,
            "external_transaction_id": transaction_id,
            "version": action.version + 1,
        }
    )
