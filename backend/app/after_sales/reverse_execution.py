"""Deterministic reverse-fulfillment disposition state machine (demo).

Closes the loop that `decide_reverse_fulfillment` leaves open: a decision that
requires a return (or a resend) can now be advanced through a governed
disposition lifecycle and dispatched through an idempotent mock outbound
adapter, mirroring the RMA state machine used in production OMS systems.
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel


class DispositionStatus(StrEnum):
    PENDING = "pending"
    RETURN_LABEL_ISSUED = "return_label_issued"
    WAREHOUSE_RECEIVED = "warehouse_received"
    INSPECTED = "inspected"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class DispositionConflict(ValueError):
    """A disposition transition is not allowed from the current status."""


# Allowed transitions. PENDING -> SETTLED is the returnless fast path
# (refund_without_return / resend_without_return); the return path goes
# through receipt -> inspection before settlement.
_ALLOWED_TRANSITIONS: dict[DispositionStatus, set[DispositionStatus]] = {
    DispositionStatus.PENDING: {DispositionStatus.RETURN_LABEL_ISSUED, DispositionStatus.SETTLED, DispositionStatus.CANCELLED},
    DispositionStatus.RETURN_LABEL_ISSUED: {DispositionStatus.WAREHOUSE_RECEIVED, DispositionStatus.CANCELLED},
    DispositionStatus.WAREHOUSE_RECEIVED: {DispositionStatus.INSPECTED},
    DispositionStatus.INSPECTED: {DispositionStatus.SETTLED},
    DispositionStatus.SETTLED: set(),
    DispositionStatus.CANCELLED: set(),
}


def advance_disposition(current: DispositionStatus, target: DispositionStatus) -> DispositionStatus:
    """Return the target status if the transition is allowed, else raise."""
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise DispositionConflict(f"illegal disposition transition: {current} -> {target}")
    return target


class ReverseDisposition(BaseModel):
    id: str
    case_id: str
    order_id: str
    sku: str
    action: str  # one of ReverseAction values (refund_without_return / replace_after_return / resend_without_return ...)
    status: DispositionStatus
    requires_return: bool
    grade: str | None = None  # quality grade from the reverse decision (a/b/c/d)
    dispatch_reference: str | None = None
    # Returns SLA: when the return was received and the deadline to finish
    # inspection/disposal (industry best practice: 24-72h after receipt).
    received_at: datetime | None = None
    sla_deadline: datetime | None = None


# Industry return-handling SLA after warehouse receipt (24h best practice,
# 48h standard, 72h acceptable) — DCL / Odoo RMA benchmarks.
RETURN_SLA_HOURS = 48


def build_disposition(
    *,
    case_id: str,
    order_id: str,
    sku: str,
    decision,
    now: datetime | None = None,
) -> ReverseDisposition:
    """Create a governed return disposition from a reverse-fulfillment decision.

    A decision that requires a return opens a disposition that will later be
    marked received (starting the SLA clock) and advanced through inspection.
    A returnless decision settles immediately — nothing to receive or inspect.
    This is the wiring point between the decision layer and the disposition
    state machine (previously the module was orphan code).
    """
    now = now or datetime.now(UTC)
    requires_return = decision.requires_return
    action = decision.action.value if hasattr(decision.action, "value") else str(decision.action)
    if not requires_return:
        return ReverseDisposition(
            id=str(uuid.uuid4()),
            case_id=case_id,
            order_id=order_id,
            sku=sku,
            action=action,
            status=DispositionStatus.SETTLED,
            requires_return=False,
            grade=decision.grade,
            dispatch_reference=None,
        )
    return ReverseDisposition(
        id=str(uuid.uuid4()),
        case_id=case_id,
        order_id=order_id,
        sku=sku,
        action=action,
        status=DispositionStatus.PENDING,
        requires_return=True,
        grade=decision.grade,
    )


def mark_received(disposition: ReverseDisposition, *, received_at: datetime, sla_hours: int = RETURN_SLA_HOURS) -> ReverseDisposition:
    """Record warehouse receipt and start the return-handling SLA clock.

    A return first gets a label issued, then is received at the warehouse —
    the receipt is what starts the 24-72h disposal SLA.
    """
    label = advance_disposition(disposition.status, DispositionStatus.RETURN_LABEL_ISSUED)
    status = advance_disposition(label, DispositionStatus.WAREHOUSE_RECEIVED)
    return disposition.model_copy(
        update={
            "status": status,
            "received_at": received_at,
            "sla_deadline": received_at + timedelta(hours=sla_hours),
        }
    )


def sla_breach(disposition: ReverseDisposition, *, now: datetime | None = None) -> bool:
    """True when the return has been received but not settled within the SLA."""
    if not disposition.requires_return or disposition.received_at is None or disposition.sla_deadline is None:
        return False
    if disposition.status is DispositionStatus.SETTLED or disposition.status is DispositionStatus.CANCELLED:
        return False
    return (now or datetime.now(UTC)) >= disposition.sla_deadline


class MockReverseExecutor:
    """In-memory adapter for outbound dispatch / restock (demo only).

    Idempotent by a deterministic key: the same dispatch request replays the
    same reference instead of creating a second outbound shipment.
    """

    def __init__(self) -> None:
        self.outbound: dict[str, str] = {}

    def dispatch(self, *, order_id: str, sku: str, kind: str, idempotency_key: str) -> str:
        if idempotency_key not in self.outbound:
            digest = hashlib.sha256(f"{order_id}:{sku}:{kind}:{idempotency_key}".encode()).hexdigest()[:16]
            self.outbound[idempotency_key] = f"MOCK-{kind.upper()}-{digest}"
        return self.outbound[idempotency_key]
