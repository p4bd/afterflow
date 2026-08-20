"""Tests for the reverse-fulfillment disposition state machine and executor."""

from datetime import UTC, datetime, timedelta

import pytest

from app.after_sales.reverse import (
    CustomerPreference,
    ReverseAction,
    ReverseDecision,
    ReverseInput,
    VisualEvidence,
    decide_reverse_fulfillment,
)
from app.after_sales.reverse_execution import (
    DispositionConflict,
    DispositionStatus,
    MockReverseExecutor,
    advance_disposition,
    build_disposition,
    mark_received,
    sla_breach,
)


def test_return_path_reaches_settlement_via_inspection():
    status = DispositionStatus.PENDING
    for target in (
        DispositionStatus.RETURN_LABEL_ISSUED,
        DispositionStatus.WAREHOUSE_RECEIVED,
        DispositionStatus.INSPECTED,
        DispositionStatus.SETTLED,
    ):
        status = advance_disposition(status, target)
    assert status is DispositionStatus.SETTLED


def test_returnless_fast_path_settles_directly():
    assert advance_disposition(DispositionStatus.PENDING, DispositionStatus.SETTLED) is DispositionStatus.SETTLED


def test_illegal_transition_is_rejected():
    with pytest.raises(DispositionConflict, match="illegal"):
        advance_disposition(DispositionStatus.WAREHOUSE_RECEIVED, DispositionStatus.SETTLED)
    with pytest.raises(DispositionConflict, match="illegal"):
        advance_disposition(DispositionStatus.SETTLED, DispositionStatus.CANCELLED)


def test_cancelled_is_terminal():
    assert advance_disposition(DispositionStatus.PENDING, DispositionStatus.CANCELLED) is DispositionStatus.CANCELLED
    with pytest.raises(DispositionConflict):
        advance_disposition(DispositionStatus.CANCELLED, DispositionStatus.INSPECTED)


def test_reverse_executor_is_idempotent_by_key():
    executor = MockReverseExecutor()

    first = executor.dispatch(order_id="ORDER-1003", sku="KETTLE-SMART", kind="resend", idempotency_key="key-1")
    second = executor.dispatch(order_id="ORDER-1003", sku="KETTLE-SMART", kind="resend", idempotency_key="key-1")

    assert first == second
    assert first.startswith("MOCK-RESEND-")
    assert len(executor.outbound) == 1

    other = executor.dispatch(order_id="ORDER-1003", sku="KETTLE-SMART", kind="restock", idempotency_key="key-2")
    assert other != first
    assert len(executor.outbound) == 2


def _decided_return() -> ReverseDecision:
    return decide_reverse_fulfillment(
        ReverseInput(
            issue_type="damaged_item",
            item_value=32_800,
            refund_amount=32_800,
            return_shipping_cost=1_200,
            handling_cost=600,
            expected_recovery_value=20_000,
            replacement_unit_cost=18_000,
            replacement_shipping_cost=800,
            replacement_inventory=5,
            customer_preference=CustomerPreference.REFUND,
            visual_evidence=VisualEvidence(damage_level="minor", human_confirmed=True),
        )
    )


def test_decision_to_disposition_to_sla_chain():
    # The disposition state machine is wired to real decisions, not orphaned.
    decision = _decided_return()
    assert decision.action is ReverseAction.RETURN_AND_REFUND

    received = datetime(2026, 8, 1, tzinfo=UTC)
    disposition = build_disposition(case_id="C-1", order_id="ORDER-1003", sku="KETTLE-SMART", decision=decision)

    assert disposition.requires_return is True
    assert disposition.grade == "b"  # carries the quality grade from the decision
    assert disposition.status is DispositionStatus.PENDING

    disposition = mark_received(disposition, received_at=received, sla_hours=48)
    assert disposition.received_at == received
    assert disposition.sla_deadline == received + timedelta(hours=48)
    assert disposition.status is DispositionStatus.WAREHOUSE_RECEIVED

    # not yet overdue
    assert sla_breach(disposition, now=received + timedelta(hours=40)) is False
    # overdue past the SLA deadline
    assert sla_breach(disposition, now=received + timedelta(hours=49)) is True
    # settling closes the SLA obligation
    settled = advance_disposition(disposition.status, DispositionStatus.INSPECTED)
    settled = advance_disposition(settled, DispositionStatus.SETTLED)
    assert sla_breach(disposition.model_copy(update={"status": settled}), now=received + timedelta(hours=49)) is False


def test_returnless_decision_settles_without_disposition_cycle():
    decision = decide_reverse_fulfillment(
        ReverseInput(
            issue_type="damaged_item",
            item_value=32_800,
            refund_amount=32_800,
            return_shipping_cost=1_200,
            handling_cost=600,
            expected_recovery_value=1_000,
            replacement_unit_cost=18_000,
            replacement_shipping_cost=800,
            replacement_inventory=5,
            customer_preference=CustomerPreference.REFUND,
            visual_evidence=VisualEvidence(damage_level="destroyed", human_confirmed=True),
        )
    )
    assert decision.action is ReverseAction.REFUND_WITHOUT_RETURN

    disposition = build_disposition(case_id="C-2", order_id="ORDER-1003", sku="KETTLE-SMART", decision=decision)
    assert disposition.requires_return is False
    assert disposition.status is DispositionStatus.SETTLED
    assert disposition.received_at is None
