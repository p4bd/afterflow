"""Natural-language intake, continuation, and reverse-action API coverage."""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.after_sales.actions import MockRefundExecutor
from app.after_sales.mock_data import PAYMENTS
from app.after_sales.operations import create_intake_case
from app.after_sales.repository import AfterSalesRepository
from app.gateway.routers.after_sales import router
from deerflow.persistence.base import Base


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'intake.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = FastAPI()
    app.state.after_sales_repo = AfterSalesRepository(async_sessionmaker(engine, expire_on_commit=False))
    app.state.after_sales_refund_executor = MockRefundExecutor(initial_balances={order_id: payment["refundable_balance"] for order_id, payment in PAYMENTS.items()})

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user = SimpleNamespace(
            id=request.headers.get("x-user-id", "agent-1"),
            system_role=request.headers.get("x-user-role", "user"),
        )
        return await call_next(request)

    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await engine.dispose()


@pytest.mark.asyncio
async def test_complaint_intake_persists_clarification_and_is_owner_scoped(client):
    created = await client.post(
        "/api/after-sales/cases/intake",
        json={"complaint_text": "耳机右边没声音，我想换一个，但不想再等一周。", "thread_id": "thread-1"},
    )
    assert created.status_code == 201
    case = created.json()
    assert case["complaint_text"].startswith("耳机右边")
    assert case["customer_expectation"] == "replacement"
    assert case["thread_id"] == "thread-1"
    assert case["status"] == "awaiting_clarification"
    assert "order_id" in case["evidence_json"]["missing"]

    own = await client.get("/api/after-sales/cases")
    other = await client.get("/api/after-sales/cases", headers={"x-user-id": "agent-2"})
    assert [item["id"] for item in own.json()] == [case["id"]]
    assert other.json() == []


@pytest.mark.asyncio
async def test_ambiguous_order_ids_are_not_guessed(client):
    created = await client.post(
        "/api/after-sales/cases/intake",
        json={"complaint_text": "不确定是 ORDER-1001 还是 ORDER-1002，那单一直没收到。"},
    )
    assert created.status_code == 201
    assert created.json()["order_id"] is None
    assert created.json()["status"] == "awaiting_clarification"


@pytest.mark.asyncio
async def test_common_broken_item_phrase_maps_to_damaged_item(client):
    created = await client.post(
        "/api/after-sales/cases/intake",
        json={"complaint_text": "订单 ORDER-1003 商品坏了，我还没决定退款还是换货。"},
    )

    assert created.status_code == 201
    assert created.json()["issue_type"] == "damaged_item"
    assert created.json()["status"] == "awaiting_evidence"


@pytest.mark.asyncio
async def test_model_supplied_fields_cannot_resolve_ambiguous_or_missing_claims():
    class RecordingRepository:
        def __init__(self):
            self.case = None

        async def create_case(self, **values):
            self.case = {"id": "CASE-1", **values}
            return self.case

        async def append_event(self, **_):
            return None

    repo = RecordingRepository()
    case = await create_intake_case(
        repo=repo,
        user_id="agent-1",
        user_role="user",
        complaint_text="不确定是 ORDER-1001 还是 ORDER-1002，我想处理售后。",
        order_id="ORDER-1001",
        issue_type="delivery_not_received",
        structured_source="model",
    )

    assert case["order_id"] is None
    assert case["issue_type"] is None
    assert case["status"] == "awaiting_clarification"
    assert case["evidence"]["intake"]["structured_source"] == "model"


@pytest.mark.asyncio
async def test_explicit_null_supplement_clears_wrong_issue(client):
    case = (
        await client.post(
            "/api/after-sales/cases/intake",
            json={"complaint_text": "ORDER-1001 没收到，要求退款"},
        )
    ).json()

    updated = await client.post(
        f"/api/after-sales/cases/{case['id']}/supplements",
        json={"issue_type": None, "note": "客户撤回此前的问题描述"},
    )

    assert updated.status_code == 200
    assert updated.json()["issue_type"] is None
    assert updated.json()["status"] == "awaiting_clarification"


@pytest.mark.asyncio
async def test_quality_issue_with_refund_preference_stays_on_refund_path(client):
    created = await client.post(
        "/api/after-sales/cases/intake",
        json={"complaint_text": "ORDER-1002 的耳机没声音了，我要退款。"},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "decided"
    assert created.json()["decision_json"]["action"] == "refund_original_payment"


@pytest.mark.asyncio
async def test_refund_intake_creates_aggregate_with_evidence_and_timeline(client):
    created = await client.post(
        "/api/after-sales/cases/intake",
        json={"complaint_text": "订单 ORDER-1001 一直没收到，我想退款。"},
    )
    assert created.status_code == 201
    case = created.json()
    assert case["order_id"] == "ORDER-1001"
    assert case["issue_type"] == "delivery_not_received"
    assert case["status"] == "decided"
    assert case["evidence_json"]["sources"]
    assert case["reply_draft"]

    detail = (await client.get(f"/api/after-sales/cases/{case['id']}")).json()
    assert detail["events"][0]["event_type"] == "intake_created"
    assert detail["actions"] == []


@pytest.mark.asyncio
async def test_supplement_reassesses_same_case_and_invalidates_old_action(client):
    case = (
        await client.post(
            "/api/after-sales/cases/intake",
            json={"complaint_text": "ORDER-1001 没收到，要求退款"},
        )
    ).json()
    action = (await client.post(f"/api/after-sales/cases/{case['id']}/actions")).json()

    updated = await client.post(
        f"/api/after-sales/cases/{case['id']}/supplements",
        json={"issue_type": "refund_amount_dispute", "note": "客户澄清为退款金额争议"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["id"] == case["id"]
    assert body["version"] > case["version"]
    assert body["issue_type"] == "refund_amount_dispute"
    assert any(event["event_type"] == "case_reassessed" for event in body["events"])
    assert body["actions"][0]["id"] == action["id"]
    assert body["actions"][0]["status"] == "rejected"


@pytest.mark.asyncio
async def test_resend_without_return_runs_through_approval_and_idempotent_dispatch(client):
    case = (
        await client.post(
            "/api/after-sales/cases/intake",
            json={"complaint_text": "订单 ORDER-1003 的水壶破损，我想换一个。"},
        )
    ).json()
    assert case["status"] == "awaiting_evidence"

    case = (
        await client.post(
            f"/api/after-sales/cases/{case['id']}/supplements",
            json={"visual_evidence_confirmed": True, "damage_level": "major", "note": "客服已人工核对照片"},
        )
    ).json()
    assert case["decision_json"]["route"] == "reverse"
    assert case["decision_json"]["reverse"]["action"] == "resend_without_return"

    action = (await client.post(f"/api/after-sales/cases/{case['id']}/actions")).json()
    assert action["action_type"] == "resend"
    assert action["status"] == "pending_approval"
    approved = (
        await client.post(
            f"/api/after-sales/actions/{action['id']}/approve",
            headers={"x-user-id": "admin-1", "x-user-role": "admin"},
            json={"expected_version": action["version"], "comment": "库存与证据已核对"},
        )
    ).json()
    executed = await client.post(
        f"/api/after-sales/actions/{action['id']}/execute",
        headers={"x-user-id": "admin-1", "x-user-role": "admin"},
        json={"expected_version": approved["version"], "payload": action["payload"]},
    )
    assert executed.status_code == 200
    assert executed.json()["external_transaction_id"].startswith("MOCK-RESEND-")

    detail = (await client.get(f"/api/after-sales/cases/{case['id']}")).json()
    assert detail["status"] == "completed"
    assert "补发" in detail["reply_draft"]
