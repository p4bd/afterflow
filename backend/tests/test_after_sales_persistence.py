"""Persistence tests for AfterFlow cases, actions, and audit events."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.after_sales.actions import approve_action, create_refund_action
from app.after_sales.repository import AfterSalesRepository, ConcurrentActionError
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
        approver_roles={"after_sales_supervisor"},
        expected_version=1,
        now=datetime(2026, 7, 13, tzinfo=UTC),
    )

    saved = await repository.save_action(approved, user_id="owner-1", expected_version=1)
    assert saved.version == 2
    with pytest.raises(ConcurrentActionError):
        await repository.save_action(approved, user_id="owner-1", expected_version=1)
