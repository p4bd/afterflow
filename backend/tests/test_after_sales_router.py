"""End-to-end API tests for the AfterFlow approval lifecycle."""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.after_sales.actions import MockRefundExecutor
from app.after_sales.mock_data import PAYMENTS
from app.after_sales.repository import AfterSalesRepository
from app.gateway.routers.after_sales import router
from deerflow.persistence.base import Base


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'after-sales-api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.state.after_sales_repo = AfterSalesRepository(async_sessionmaker(engine, expire_on_commit=False))
    app.state.after_sales_refund_executor = MockRefundExecutor(
        initial_balances={order_id: payment["refundable_balance"] for order_id, payment in PAYMENTS.items()}
    )

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
async def test_agent_supervisor_execution_lifecycle(client):
    created = await client.post(
        "/api/after-sales/cases",
        json={
            "order_id": "ORDER-1001",
            "issue_type": "delivery_not_received",
            "operator_refund_limit": 20_000,
        },
    )
    assert created.status_code == 201
    case = created.json()
    action_response = await client.post(f"/api/after-sales/cases/{case['id']}/actions")
    assert action_response.status_code == 201
    action = action_response.json()
    assert action["status"] == "pending_approval"

    queue = await client.get(
        "/api/after-sales/actions?action_status=pending_approval",
        headers={"x-user-id": "admin-1", "x-user-role": "admin"},
    )
    assert [item["id"] for item in queue.json()] == [action["id"]]

    approved_response = await client.post(
        f"/api/after-sales/actions/{action['id']}/approve",
        headers={"x-user-id": "admin-1", "x-user-role": "admin"},
        json={"expected_version": 1, "comment": "证据完整"},
    )
    assert approved_response.status_code == 200
    approved = approved_response.json()
    assert approved["status"] == "approved"

    executed_response = await client.post(
        f"/api/after-sales/actions/{action['id']}/execute",
        headers={"x-user-id": "admin-1", "x-user-role": "admin"},
        json={"expected_version": 2, "payload": action["payload"]},
    )
    assert executed_response.status_code == 200
    executed = executed_response.json()
    assert executed["status"] == "completed"
    assert executed["external_transaction_id"].startswith("MOCK-REFUND-")


@pytest.mark.asyncio
async def test_non_admin_cannot_approve(client):
    response = await client.post(
        "/api/after-sales/actions/not-found/approve",
        json={"expected_version": 1},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_operator_limit_is_not_client_controllable(client):
    # A caller that claims a huge operator_refund_limit in the body must not be
    # able to turn a 90900 refund into auto-approval: the limit is resolved
    # server-side from the authenticated role (default user -> 20000).
    created = await client.post(
        "/api/after-sales/cases",
        json={
            "order_id": "ORDER-1001",
            "issue_type": "delivery_not_received",
            "operator_refund_limit": 9_999_999,
        },
    )
    assert created.status_code == 201
    decision = created.json()["decision_json"]

    assert decision["approval_required"] is True
    assert decision["refund_amount"] == 90_900
    assert "exceeds_operator_limit" in decision["approval_reasons"]
