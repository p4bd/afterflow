"""Deterministic approval and idempotent refund execution boundary."""

import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from .mock_data import PAYMENTS
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
    # Real auth role (see auth/models.py system_role Literal["admin","user"]).
    # Converged from the previous ghost role "after_sales_supervisor" which was
    # never actually assigned to any user and only matched a hardcoded set.
    required_role: str = "admin"
    idempotency_key: str
    approved_by: str | None = None
    comment: str | None = None
    approved_at: datetime | None = None
    expires_at: datetime
    external_transaction_id: str | None = None
    version: int = Field(default=1, ge=1)
    # Four-eyes support: how many distinct approvers must sign before the
    # action becomes APPROVED (1 = maker-checker, 2 = four-eyes dual approval).
    approvers_required: int = Field(default=1, ge=1)
    approver_ids: list[str] = Field(default_factory=list)
    # True once the funds have been frozen for this action (authorize step).
    # Persisted so a reaper can release reservations on expiry, and so we never
    # release money for an action that never reserved it.
    reserved: bool = False


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
    if decision.action not in {ResolutionAction.REFUND_ORIGINAL_PAYMENT, ResolutionAction.RETURN_AND_REFUND} or decision.refund_amount <= 0:
        raise ValueError("decision does not contain an executable refund")
    now = now or datetime.now(UTC)
    payload = {
        "order_id": order_id,
        "amount": decision.refund_amount,
        **({"requires_return": True} if decision.action is ResolutionAction.RETURN_AND_REFUND else {}),
    }
    auto_approved = not decision.approval_required and decision.risk_level is not RiskLevel.HIGH
    # Deterministic business idempotency key: the same case + action + payload
    # always derives the same key, so a retry replays instead of issuing a
    # second refund (mirrors the Stripe Idempotency-Key contract). The key is
    # stored with a UNIQUE constraint in the database.
    idempotency_key = hashlib.sha256(f"{case_id}:refund:{payload_hash(payload)}".encode()).hexdigest()
    # Risk tier "four_eyes" (high-risk + high amount) requires two distinct
    # approvers; everything else needs a single sign-off.
    approvers_required = 2 if decision.risk_tier == "four_eyes" else 1
    return ActionRequest(
        id=str(uuid.uuid4()),
        case_id=case_id,
        payload=payload,
        payload_hash=payload_hash(payload),
        status=ActionStatus.APPROVED if auto_approved else ActionStatus.PENDING_APPROVAL,
        risk_level=decision.risk_level,
        requested_by=requested_by,
        idempotency_key=idempotency_key,
        approved_by="system-policy" if auto_approved else None,
        approved_at=now if auto_approved else None,
        expires_at=now + ttl,
        approvers_required=approvers_required,
        approver_ids=[],
    )


def create_resend_action(
    *,
    case_id: str,
    order_id: str,
    sku: str,
    requested_by: str,
    estimated_cost: int,
    now: datetime | None = None,
    ttl: timedelta = timedelta(hours=24),
) -> ActionRequest:
    """Create the single connected reverse action: returnless replacement dispatch."""
    now = now or datetime.now(UTC)
    payload = {"order_id": order_id, "sku": sku, "kind": "resend", "estimated_cost": estimated_cost}
    return ActionRequest(
        id=str(uuid.uuid4()),
        case_id=case_id,
        action_type="resend",
        payload=payload,
        payload_hash=payload_hash(payload),
        status=ActionStatus.PENDING_APPROVAL,
        risk_level=RiskLevel.MEDIUM,
        requested_by=requested_by,
        idempotency_key=hashlib.sha256(f"{case_id}:resend:{payload_hash(payload)}".encode()).hexdigest(),
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
    balance_check: Callable[..., None] | None = None,
) -> ActionRequest:
    now = now or datetime.now(UTC)
    _check_decision(action, approver_id, approver_roles, expected_version, now)
    # P0-3: re-check the available+reserved pool BEFORE flipping the action
    # to APPROVED. If the pool has drained since create_refund_action, fail
    # fast with ActionConflict rather than queuing an execute-time failure.
    # The check is opt-in (None = skip) so unit tests that don't care about
    # the executor can still drive the state machine.
    if balance_check is not None and not action.reserved:
        balance_check(order_id=action.payload.get("order_id", ""), amount=action.payload["amount"])
    if approver_id in action.approver_ids:
        raise ActionForbidden("approver already approved this action")
    approver_ids = [*action.approver_ids, approver_id]
    # Four-eyes: the action stays pending until the required count of distinct
    # approvers has signed. Maker-checker (1) becomes APPROVED immediately.
    status = ActionStatus.APPROVED if len(approver_ids) >= action.approvers_required else ActionStatus.PENDING_APPROVAL
    return action.model_copy(
        update={
            "status": status,
            "approved_by": approver_id,
            "approved_at": now,
            "comment": comment,
            "approver_ids": approver_ids,
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
    # Maker-checker: the requester can never approve (or reject) their own
    # money action, regardless of risk level. This matches the industry norm
    # that even an admin cannot self-approve a funds movement.
    if action.requested_by == approver_id:
        raise ActionForbidden("requester cannot approve own action")


class MockRefundExecutor:
    """In-memory payment adapter used only by the local demo and tests.

    Implements an authorize-then-capture lifecycle (mirrors Stripe): approval
    RESERVES the amount (available -> reserved), execution CAPTURES from the
    reservation, and expiry/rejection RELEASES it back. The decrement is atomic
    under a lock so concurrent refunds cannot overspend a reservation.
    """

    # Refund settlement metadata (demo): funds go back to the original payment
    # channel; the mock reports "processing" with an ETA, then can be settled.
    REFUND_CHANNEL = "original_payment"
    REFUND_ETA_HOURS = 24

    def __init__(self, initial_balances: dict[str, int] | None = None) -> None:
        self.transactions: dict[str, str] = {}
        self._available: dict[str, int] = dict(initial_balances) if initial_balances else {}
        self._reserved: dict[str, int] = {}
        self._settled: set[str] = set()
        self._lock = threading.Lock()

    def balance_of(self, order_id: str) -> int:
        """Remaining AVAILABLE balance (what a fresh decision can rely on)."""
        return self._available.get(order_id, 0)

    def reserved_of(self, order_id: str) -> int:
        """Amount currently frozen for approved refunds on this order."""
        return self._reserved.get(order_id, 0)

    def reserve(self, *, order_id: str, amount: int) -> bool:
        """Move amount from available to reserved. Returns False if insufficient."""
        with self._lock:
            available = self._available.get(order_id, 0)
            if available < amount:
                return False
            self._available[order_id] = available - amount
            self._reserved[order_id] = self._reserved.get(order_id, 0) + amount
            return True

    def release(self, *, order_id: str, amount: int) -> None:
        """Return a reservation back to available (expiry / rejection)."""
        with self._lock:
            reserved = self._reserved.get(order_id, 0)
            released = min(reserved, amount)
            self._reserved[order_id] = reserved - released
            self._available[order_id] = self._available.get(order_id, 0) + released

    def is_settled(self, idempotency_key: str) -> bool:
        return idempotency_key in self._settled

    def settle(self, idempotency_key: str) -> None:
        """Flip a processing refund to succeeded (demo: the bank confirmed)."""
        if idempotency_key in self.transactions:
            self._settled.add(idempotency_key)

    def refund(self, *, order_id: str, amount: int, idempotency_key: str) -> str:
        with self._lock:
            if idempotency_key not in self.transactions:
                reserved = self._reserved.get(order_id, 0)
                if reserved < amount:
                    raise ActionConflict("insufficient reserved refund balance")
                digest = hashlib.sha256(f"{order_id}:{amount}:{idempotency_key}".encode()).hexdigest()[:16]
                self.transactions[idempotency_key] = f"MOCK-REFUND-{digest}"
                self._reserved[order_id] = reserved - amount
            return self.transactions[idempotency_key]


_mock_refund_executor = MockRefundExecutor(initial_balances={order_id: payment["refundable_balance"] for order_id, payment in PAYMENTS.items()})


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
    # The submitted payload must match the hash the approver signed. The stored
    # payload is assumed consistent with its stored hash (written atomically).
    if payload_hash(payload) != action.payload_hash:
        raise ActionConflict("payload hash mismatch")
    if action.payload.get("requires_return"):
        raise ActionConflict("return receipt and inspection required before refund execution")
    if action.idempotency_key not in executor.transactions and current_refundable_balance < action.payload["amount"]:
        raise ActionConflict("refundable balance changed")

    transaction_id = executor.refund(
        order_id=action.payload["order_id"],
        amount=action.payload["amount"],
        idempotency_key=action.idempotency_key,
    )
    return action.model_copy(
        update={
            "status": ActionStatus.COMPLETED,
            "external_transaction_id": transaction_id,
            "version": action.version + 1,
        }
    )


def execute_approved_resend(
    action: ActionRequest,
    *,
    payload: dict,
    expected_version: int,
    executor,
    now: datetime | None = None,
) -> ActionRequest:
    now = now or datetime.now(UTC)
    if action.action_type != "resend":
        raise ActionConflict("action is not a resend")
    if action.status is not ActionStatus.APPROVED:
        raise ActionForbidden("action is not approved")
    if action.version != expected_version:
        raise ActionConflict("action version conflict")
    if now >= action.expires_at:
        raise ActionConflict("action expired")
    if payload_hash(payload) != action.payload_hash:
        raise ActionConflict("payload hash mismatch")
    reference = executor.dispatch(
        order_id=action.payload["order_id"],
        sku=action.payload["sku"],
        kind="resend",
        idempotency_key=action.idempotency_key,
    )
    return action.model_copy(update={"status": ActionStatus.COMPLETED, "external_transaction_id": reference, "version": action.version + 1})
