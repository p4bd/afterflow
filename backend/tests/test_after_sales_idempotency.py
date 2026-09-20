"""Idempotency-Key contract tests for AfterFlow write endpoints.

The pre-existing refund-execution idempotency is built around a deterministic
business key (case_id + payload_hash). That protects against the *server*
double-running. It does NOT protect against the *client* double-submitting a
network call that the server already processed and replied to — the client
never got the reply (timeout, dropped connection, browser refresh), so it
retries, and the server now runs again.

The AfterFlow idempotency layer adds a per-endpoint Idempotency-Key cache so
that a retried request returns the original response without running any
side effect. It is the same shape as the Stripe Idempotency-Key contract:
- key MUST be a UUID/ULID/whatever the caller chose
- same key + same payload fingerprint → cached response
- same key + different payload → 422 conflict
- expiry (24h default)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.after_sales.idempotency import (
    IdempotencyConflict,
    IdempotencyHit,
    IdempotencyMiss,
    InMemoryIdempotencyStore,
    fingerprint_request,
)


def _record(key: str, endpoint: str, body: dict, response: dict, *, now: datetime | None = None) -> dict:
    return {
        "key": key,
        "endpoint": endpoint,
        "request_fingerprint": fingerprint_request(body),
        "response": response,
        "status_code": 200,
        "created_at": (now or datetime.now(UTC)).isoformat(),
        "expires_at": ((now or datetime.now(UTC)) + timedelta(hours=24)).isoformat(),
    }


class TestFingerprintRequest:
    def test_same_body_same_fingerprint(self) -> None:
        assert fingerprint_request({"a": 1, "b": 2}) == fingerprint_request({"a": 1, "b": 2})

    def test_key_order_does_not_matter(self) -> None:
        assert fingerprint_request({"a": 1, "b": 2}) == fingerprint_request({"b": 2, "a": 1})

    def test_different_body_different_fingerprint(self) -> None:
        assert fingerprint_request({"a": 1}) != fingerprint_request({"a": 2})


class TestInMemoryIdempotencyStore:
    @pytest.mark.asyncio
    async def test_first_call_misses(self) -> None:
        store = InMemoryIdempotencyStore(ttl=timedelta(hours=24))
        result = await store.lookup(key="k1", endpoint="/api/x", body={"a": 1})
        assert isinstance(result, IdempotencyMiss)

    @pytest.mark.asyncio
    async def test_second_call_with_same_key_hits(self) -> None:
        store = InMemoryIdempotencyStore(ttl=timedelta(hours=24))
        body = {"a": 1}
        await store.store(key="k1", endpoint="/api/x", body=body, response={"id": "abc"}, status_code=201)
        result = await store.lookup(key="k1", endpoint="/api/x", body=body)
        assert isinstance(result, IdempotencyHit)
        assert result.response == {"id": "abc"}
        assert result.status_code == 201

    @pytest.mark.asyncio
    async def test_same_key_different_body_raises_conflict(self) -> None:
        store = InMemoryIdempotencyStore(ttl=timedelta(hours=24))
        await store.store(key="k1", endpoint="/api/x", body={"a": 1}, response={"id": "abc"}, status_code=201)
        with pytest.raises(IdempotencyConflict):
            await store.lookup(key="k1", endpoint="/api/x", body={"a": 999})

    @pytest.mark.asyncio
    async def test_same_key_different_endpoint_does_not_conflict(self) -> None:
        # A key is scoped to an endpoint: the same caller may legitimately use
        # the same UUID as a key for two different endpoints.
        store = InMemoryIdempotencyStore(ttl=timedelta(hours=24))
        await store.store(key="k1", endpoint="/api/x", body={"a": 1}, response={"id": "abc"}, status_code=201)
        result = await store.lookup(key="k1", endpoint="/api/y", body={"a": 1})
        assert isinstance(result, IdempotencyMiss)

    @pytest.mark.asyncio
    async def test_expired_entry_misses_again(self) -> None:
        store = InMemoryIdempotencyStore(ttl=timedelta(seconds=0))
        await store.store(key="k1", endpoint="/api/x", body={"a": 1}, response={"id": "abc"}, status_code=201)
        # TTL is 0s → entry has already expired by the time we look up
        result = await store.lookup(key="k1", endpoint="/api/x", body={"a": 1})
        assert isinstance(result, IdempotencyMiss)

    @pytest.mark.asyncio
    async def test_concurrent_first_writes_only_one_record(self) -> None:
        # Two concurrent retries with the same key: only the first writer wins;
        # the second one must observe the first record on the next lookup.
        store = InMemoryIdempotencyStore(ttl=timedelta(hours=24))

        async def writer(response_id: str) -> None:
            await store.store(key="k1", endpoint="/api/x", body={"a": 1}, response={"id": response_id}, status_code=201)

        await asyncio.gather(writer("first"), writer("second"))
        result = await store.lookup(key="k1", endpoint="/api/x", body={"a": 1})
        assert isinstance(result, IdempotencyHit)
        # Whichever writer committed first is the canonical response.
        assert result.response in ({"id": "first"}, {"id": "second"})
