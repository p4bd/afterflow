"""AfterFlow router tests for Idempotency-Key on every write endpoint.

The Stripe-style contract: client may safely retry the same write request, the
server will run it at most once. We verify on case-create, approve, and
execute — these are the money-moving endpoints.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.after_sales.actions import MockRefundExecutor
from app.after_sales.idempotency import InMemoryIdempotencyStore
from app.after_sales.mock_data import PAYMENTS
from app.after_sales.repository import AfterSalesRepository
from app.gateway.routers.after_sales import router
from deerflow.persistence.base import Base

AGENT = {"x-user-id": "agent-1", "x-user-role": "user"}
ADMIN = {"x-user-id": "admin-1", "x-user-role": "admin"}


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'after-sales-idem.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.state.after_sales_repo = AfterSalesRepository(async_sessionmaker(engine, expire_on_commit=False))
    app.state.after_sales_refund_executor = MockRefundExecutor(initial_balances={order_id: payment["refundable_balance"] for order_id, payment in PAYMENTS.items()})
    app.state.after_sales_idempotency_store = InMemoryIdempotencyStore()

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


async def _setup_case_and_action(client: httpx.AsyncClient) -> tuple[str, str]:
    case = await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1001", "issue_type": "delivery_not_received", "visual_evidence_confirmed": False},
        headers=AGENT,
    )
    case_id = case.json()["id"]
    action = await client.post(f"/api/after-sales/cases/{case_id}/actions", headers=AGENT)
    return case_id, action.json()["id"]


@pytest.mark.asyncio
async def test_create_case_with_same_idempotency_key_replays(client):
    body = {"order_id": "ORDER-1001", "issue_type": "delivery_not_received", "visual_evidence_confirmed": False}
    r1 = await client.post("/api/after-sales/cases", json=body, headers={**AGENT, "Idempotency-Key": "case-1"})
    r2 = await client.post("/api/after-sales/cases", json=body, headers={**AGENT, "Idempotency-Key": "case-1"})
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert r1.json()["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_intake_with_same_idempotency_key_replays(client):
    body = {"complaint_text": "ORDER-1001 一直没收到，我想退款。"}
    headers = {**AGENT, "Idempotency-Key": "intake-1"}

    first = await client.post("/api/after-sales/cases/intake", json=body, headers=headers)
    second = await client.post("/api/after-sales/cases/intake", json=body, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_action_creation_and_supplement_replay(client):
    case_id, first_action_id = await _setup_case_and_action(client)

    replayed_action = await client.post(
        f"/api/after-sales/cases/{case_id}/actions",
        headers={**AGENT, "Idempotency-Key": "action-1"},
    )
    assert replayed_action.status_code == 409

    supplement = {"note": "客户再次确认问题", "issue_type": "delivery_not_received"}
    headers = {**AGENT, "Idempotency-Key": "supplement-1"}
    first = await client.post(f"/api/after-sales/cases/{case_id}/supplements", json=supplement, headers=headers)
    second = await client.post(f"/api/after-sales/cases/{case_id}/supplements", json=supplement, headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json()["version"] == second.json()["version"]
    assert first.json()["actions"][0]["id"] == first_action_id


@pytest.mark.asyncio
async def test_action_creation_with_same_idempotency_key_replays(client):
    case = await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1001", "issue_type": "delivery_not_received", "visual_evidence_confirmed": False},
        headers=AGENT,
    )
    case_id = case.json()["id"]
    headers = {**AGENT, "Idempotency-Key": "action-create-1"}

    first = await client.post(f"/api/after-sales/cases/{case_id}/actions", headers=headers)
    second = await client.post(f"/api/after-sales/cases/{case_id}/actions", headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_same_idempotency_key_is_scoped_to_authenticated_user(client):
    body = {"order_id": "ORDER-1001", "issue_type": "delivery_not_received", "visual_evidence_confirmed": False}
    alice = await client.post(
        "/api/after-sales/cases",
        json=body,
        headers={"x-user-id": "alice", "x-user-role": "user", "Idempotency-Key": "shared-key"},
    )
    bob = await client.post(
        "/api/after-sales/cases",
        json=body,
        headers={"x-user-id": "bob", "x-user-role": "user", "Idempotency-Key": "shared-key"},
    )

    assert alice.status_code == 201
    assert bob.status_code == 201
    assert alice.json()["id"] != bob.json()["id"]
    assert alice.json()["user_id"] == "alice"
    assert bob.json()["user_id"] == "bob"


@pytest.mark.asyncio
async def test_concurrent_first_requests_run_once(client):
    body = {"order_id": "ORDER-1001", "issue_type": "delivery_not_received", "visual_evidence_confirmed": False}
    headers = {**AGENT, "Idempotency-Key": "concurrent-case"}

    first, second = await asyncio.gather(
        client.post("/api/after-sales/cases", json=body, headers=headers),
        client.post("/api/after-sales/cases", json=body, headers=headers),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    cases = await client.get("/api/after-sales/cases", headers=AGENT)
    assert len(cases.json()) == 1


@pytest.mark.asyncio
async def test_create_case_with_same_key_different_body_returns_422(client):
    await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1001", "issue_type": "delivery_not_received", "visual_evidence_confirmed": False},
        headers={**AGENT, "Idempotency-Key": "case-x"},
    )
    r = await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1002", "issue_type": "delivery_not_received", "visual_evidence_confirmed": False},
        headers={**AGENT, "Idempotency-Key": "case-x"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_approve_with_same_idempotency_key_replays(client):
    _, action_id = await _setup_case_and_action(client)
    body = {"expected_version": 1, "comment": "ok"}
    r1 = await client.post(f"/api/after-sales/actions/{action_id}/approve", json=body, headers={**ADMIN, "Idempotency-Key": "ap-1"})
    r2 = await client.post(f"/api/after-sales/actions/{action_id}/approve", json=body, headers={**ADMIN, "Idempotency-Key": "ap-1"})
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["version"] == r2.json()["version"]


@pytest.mark.asyncio
async def test_execute_with_same_idempotency_key_replays(client):
    _, action_id = await _setup_case_and_action(client)
    await client.post(f"/api/after-sales/actions/{action_id}/approve", json={"expected_version": 1, "comment": "ok"}, headers=ADMIN)
    payload = {"expected_version": 2, "payload": {"order_id": "ORDER-1001", "amount": 90900}}
    r1 = await client.post(f"/api/after-sales/actions/{action_id}/execute", json=payload, headers={**ADMIN, "Idempotency-Key": "ex-1"})
    r2 = await client.post(f"/api/after-sales/actions/{action_id}/execute", json=payload, headers={**ADMIN, "Idempotency-Key": "ex-1"})
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["external_transaction_id"] == r2.json()["external_transaction_id"]


@pytest.mark.asyncio
async def test_approve_rejected_with_409_when_balance_drained(client):
    """P0-3: if the pool is drained between create and approve, refuse the
    approval with 409 — do not queue a doomed action."""
    _, action_id = await _setup_case_and_action(client)
    # Drain the pool to below the requested amount
    executor = client._transport.app.state.after_sales_refund_executor  # type: ignore[attr-defined]
    executor.reserve(order_id="ORDER-1001", amount=85_000)
    r = await client.post(
        f"/api/after-sales/actions/{action_id}/approve",
        json={"expected_version": 1, "comment": "ok"},
        headers=ADMIN,
    )
    assert r.status_code == 409, r.text
    assert "insufficient" in r.json()["detail"].lower()
