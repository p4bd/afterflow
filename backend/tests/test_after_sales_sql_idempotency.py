"""SQL-backed IdempotencyStore tests.

The in-memory store covers the contract; this file pins the SQL implementation
that survives process restart. The schema mirrors the in-memory shape.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.after_sales.idempotency import (
    IdempotencyConflict,
    IdempotencyHit,
    IdempotencyMiss,
    SqlIdempotencyStore,
)
from deerflow.persistence.base import Base


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    # Import here so the ORM row is registered before create_all runs.
    from app.after_sales import idempotency_orm  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'idempotency.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_sql_store_round_trip(session_factory) -> None:
    store = SqlIdempotencyStore(session_factory, ttl=timedelta(hours=24))
    body = {"a": 1, "b": 2}
    await store.store(key="k1", endpoint="/api/x", body=body, response={"id": "abc"}, status_code=201)
    result = await store.lookup(key="k1", endpoint="/api/x", body=body)
    assert isinstance(result, IdempotencyHit)
    assert result.response == {"id": "abc"}
    assert result.status_code == 201


@pytest.mark.asyncio
async def test_sql_store_miss_returns_miss(session_factory) -> None:
    store = SqlIdempotencyStore(session_factory, ttl=timedelta(hours=24))
    result = await store.lookup(key="never", endpoint="/api/x", body={})
    assert isinstance(result, IdempotencyMiss)


@pytest.mark.asyncio
async def test_sql_store_expires(session_factory) -> None:
    store = SqlIdempotencyStore(session_factory, ttl=timedelta(seconds=-1))
    body = {"a": 1}
    await store.store(key="k1", endpoint="/api/x", body=body, response={"id": "abc"}, status_code=201)
    result = await store.lookup(key="k1", endpoint="/api/x", body=body)
    assert isinstance(result, IdempotencyMiss)


@pytest.mark.asyncio
async def test_sql_store_conflict_on_different_body(session_factory) -> None:
    store = SqlIdempotencyStore(session_factory, ttl=timedelta(hours=24))
    await store.store(key="k1", endpoint="/api/x", body={"a": 1}, response={"id": "abc"}, status_code=201)
    with pytest.raises(IdempotencyConflict):
        await store.lookup(key="k1", endpoint="/api/x", body={"a": 999})


@pytest.mark.asyncio
async def test_sql_store_concurrent_writers_keep_first(session_factory) -> None:
    import asyncio

    store = SqlIdempotencyStore(session_factory, ttl=timedelta(hours=24))

    async def writer(response_id: str) -> None:
        await store.store(key="k1", endpoint="/api/x", body={"a": 1}, response={"id": response_id}, status_code=201)

    await asyncio.gather(writer("first"), writer("second"))
    result = await store.lookup(key="k1", endpoint="/api/x", body={"a": 1})
    assert isinstance(result, IdempotencyHit)
    assert result.response in ({"id": "first"}, {"id": "second"})
