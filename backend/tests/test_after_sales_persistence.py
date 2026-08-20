"""Persistence tests for AfterFlow cases, actions, and audit events."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.after_sales.actions import MockRefundExecutor, approve_action, create_refund_action
from app.after_sales.repository import AfterSalesRepository, ConcurrentActionError, release_expired_reservations
from app.after_sales.schemas import DecisionResult, Eligibility, ResolutionAction, RiskLevel
from deerflow.persistence.after_sales.model import ActionRequestRow, CaseEventRow, ServiceCaseRow
from deerflow.persistence.base import Base


@pytest_asyncio.fixture
async def repository(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'after-sales.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield AfterSalesRepository(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


def _decision():
    return DecisionResult(
        eligibility=Eligibility.ELIGIBLE_WITH_APPROVAL,
        action=ResolutionAction.REFUND_ORIGINAL_PAYMENT,
        refund_amount=90_900,
        risk_level=RiskLevel.MEDIUM,
        reason_code="POLICY_MATCHED",
        approval_required=True,
        approval_reasons=["exceeds_operator_limit"],
        policy_refs=["AFTER-SALES-CN@2026.07"],
    )


@pytest.mark.asyncio
async def test_three_business_tables_are_registered():
    assert {ServiceCaseRow.__tablename__, ActionRequestRow.__tablename__, CaseEventRow.__tablename__} <= set(Base.metadata.tables)


@pytest.mark.asyncio
async def test_case_action_and_event_round_trip_is_owner_scoped(repository):
    case = await repository.create_case(
        user_id="owner-1",
        thread_id="thread-1",
        order_id="ORDER-1001",
        issue_type="delivery_not_received",
        evidence={"logistics": "no_pod"},
        decision=_decision().model_dump(mode="json"),
    )
    action = create_refund_action(
        case_id=case["id"],
        order_id="ORDER-1001",
        requested_by="owner-1",
        decision=_decision(),
        now=datetime(2026, 7, 13, tzinfo=UTC),
    )
    await repository.create_action(action, user_id="owner-1")
    await repository.append_event(case_id=case["id"], user_id="owner-1", actor="owner-1", event_type="action_proposed")

    assert await repository.get_case(case["id"], user_id="other") is None
    assert await repository.get_action(action.id, user_id="other") is None
    assert (await repository.get_action(action.id, user_id="owner-1")).payload_hash == action.payload_hash
    assert [event["event_type"] for event in await repository.list_events(case["id"], user_id="owner-1")] == ["action_proposed"]


@pytest.mark.asyncio
async def test_audit_event_chain_is_tamper_evident(repository):
    case = await repository.create_case(
        user_id="owner-1",
        thread_id=None,
        order_id="ORDER-1001",
        issue_type="delivery_not_received",
        evidence={},
        decision={},
    )
    for event_type in ("decision_generated", "approval_requested", "approval_granted", "execution_succeeded"):
        await repository.append_event(case_id=case["id"], user_id="owner-1", actor="owner-1", event_type=event_type)

    assert await repository.verify_event_chain(case["id"]) == {"valid": True, "broken_at": None, "count": 4}

    # Tamper with a past event's metadata -> the chain must break.
    async with repository._sf() as session:
        row = (await session.scalars(select(CaseEventRow).where(CaseEventRow.case_id == case["id"]))).first()
        row.event_metadata = {"tampered": True}
        await session.commit()

    result = await repository.verify_event_chain(case["id"])
    assert result["valid"] is False
    assert result["broken_at"] in {0, 1}


@pytest.mark.asyncio
async def test_reaper_releases_expired_reservations(repository):
    case = await repository.create_case(
        user_id="owner-1", thread_id=None, order_id="ORDER-1001",
        issue_type="delivery_not_received", evidence={}, decision=_decision().model_dump(mode="json"),
    )
    action = create_refund_action(case_id=case["id"], order_id="ORDER-1001", requested_by="owner-1",
                                  decision=_decision(), now=datetime(2026, 7, 13, tzinfo=UTC))
    action = await repository.create_action(action, user_id="owner-1")
    approved = approve_action(action, approver_id="admin-1", approver_roles={"admin"}, expected_version=1,
                              now=datetime(2026, 7, 13, tzinfo=UTC))
    approved = approved.model_copy(update={"reserved": True})
    approved = await repository.save_action(approved, user_id="owner-1", expected_version=1)

    # Force the approval to have expired.
    async with repository._sf() as session:
        row = (await session.scalars(select(ActionRequestRow).where(ActionRequestRow.id == approved.id))).one()
        row.expires_at = datetime(2026, 7, 1, tzinfo=UTC)
        await session.commit()

    executor = MockRefundExecutor(initial_balances={"ORDER-1001": 90_900})
    assert executor.reserve(order_id="ORDER-1001", amount=90_900) is True

    released = await release_expired_reservations(repository, executor, now=datetime(2026, 7, 13, tzinfo=UTC))

    assert released == 1
    assert executor.reserved_of("ORDER-1001") == 0
    assert executor.balance_of("ORDER-1001") == 90_900
    saved = await repository.get_action(approved.id, user_id=None)
    assert saved.reserved is False


@pytest.mark.asyncio
async def test_action_save_uses_database_optimistic_lock(repository):
    case = await repository.create_case(
        user_id="owner-1",
        thread_id=None,
        order_id="ORDER-1001",
        issue_type="delivery_not_received",
        evidence={},
        decision=_decision().model_dump(mode="json"),
    )
    action = create_refund_action(
        case_id=case["id"],
        order_id="ORDER-1001",
        requested_by="owner-1",
        decision=_decision(),
        now=datetime(2026, 7, 13, tzinfo=UTC),
    )
    await repository.create_action(action, user_id="owner-1")
    approved = approve_action(
        action,
        approver_id="admin-1",
        approver_roles={"admin"},
        expected_version=1,
        now=datetime(2026, 7, 13, tzinfo=UTC),
    )

    saved = await repository.save_action(approved, user_id="owner-1", expected_version=1)
    assert saved.version == 2
    with pytest.raises(ConcurrentActionError):
        await repository.save_action(approved, user_id="owner-1", expected_version=1)


@pytest.mark.asyncio
async def test_partial_unique_index_blocks_second_active_action(tmp_path):
    # Bypass the app-layer active-action check and hit the DB constraint
    # directly: inserting two pending actions for the same case must violate
    # the partial unique index (the TOCTOU backstop).
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'active-idx.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    async with sf() as session:
        session.add(ServiceCaseRow(id="C-1", user_id="owner-1", thread_id=None, order_id="ORDER-1001",
                                   issue_type="delivery_not_received", status="decided",
                                   evidence_json={}, decision_json={}))
        await session.commit()

    now = datetime(2026, 7, 13, tzinfo=UTC)
    first = create_refund_action(case_id="C-1", order_id="ORDER-1001", requested_by="owner-1",
                                 decision=_decision(), now=now)
    first.id = "A-1"
    async with sf() as session:
        session.add(ActionRequestRow(**first.model_dump()))
        await session.commit()

    second = first.model_copy()
    second.id = "A-2"
    second.idempotency_key = "different-key"
    async with sf() as session:
        session.add(ActionRequestRow(**second.model_dump()))
        with pytest.raises(IntegrityError):
            await session.commit()

    await engine.dispose()
