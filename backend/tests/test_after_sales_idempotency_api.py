"""End-to-end API integration of the Idempotency-Key contract."""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.after_sales.idempotency import (
    IdempotencyConflict,
    InMemoryIdempotencyStore,
    apply_idempotency,
)


@pytest_asyncio.fixture
async def client():
    app = FastAPI()
    store = InMemoryIdempotencyStore(ttl=timedelta(hours=24))
    calls: list[dict] = []

    @app.exception_handler(IdempotencyConflict)
    async def _conflict(_, exc: IdempotencyConflict):  # type: ignore[no-untyped-def]
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.post("/orders")
    async def create_order(payload: dict, idempotency_key: str | None = Header(default=None)) -> dict:
        outcome = await apply_idempotency(store, key=idempotency_key, endpoint="/orders", body=payload)
        if outcome.replay:
            return {**outcome.cached_response, "_replayed": True}
        order_id = f"O-{abs(hash(tuple(sorted(payload.items())))) % 10000}"
        response = {"order_id": order_id, "amount": payload.get("amount", 0)}
        calls.append({"order_id": order_id, "payload": payload})
        await store.store(
            key=idempotency_key or f"_auto_{len(calls)}",
            endpoint="/orders",
            body=payload,
            response=response,
            status_code=201,
        )
        return response

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, calls


@pytest.mark.asyncio
async def test_no_key_each_call_runs_a_new_side_effect(client) -> None:
    ac, calls = client
    r1 = await ac.post("/orders", json={"amount": 100})
    r2 = await ac.post("/orders", json={"amount": 100})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_same_key_same_body_replays(client) -> None:
    ac, calls = client
    body = {"amount": 100}
    r1 = await ac.post("/orders", json=body, headers={"Idempotency-Key": "abc-1"})
    r2 = await ac.post("/orders", json=body, headers={"Idempotency-Key": "abc-1"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["order_id"] == r2.json()["order_id"]
    assert r2.json().get("_replayed") is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_same_key_different_body_returns_422(client) -> None:
    ac, _ = client
    await ac.post("/orders", json={"amount": 100}, headers={"Idempotency-Key": "k"})
    r = await ac.post("/orders", json={"amount": 999}, headers={"Idempotency-Key": "k"})
    assert r.status_code == 422
    assert "different" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_different_keys_dont_share_cache(client) -> None:
    ac, calls = client
    body = {"amount": 100}
    await ac.post("/orders", json=body, headers={"Idempotency-Key": "k1"})
    await ac.post("/orders", json=body, headers={"Idempotency-Key": "k2"})
    assert len(calls) == 2
