"""Per-endpoint Idempotency-Key cache for AfterFlow write endpoints.

Stripe-style contract: the caller supplies a key (UUID/ULID); the server runs
the request once and caches the response keyed by (endpoint, key). Retries
return the cached response without re-running side effects.

Two failure modes the contract must guard against:
- Same key + different request body → 422 IdempotencyConflict
  (the caller is reusing a key for a logically different request, which is
  almost certainly a bug — refuse rather than guess).
- Same key + same body + different endpoint → not a conflict.
  The key is scoped to the endpoint, not global.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


class IdempotencyConflict(Exception):
    """The same Idempotency-Key was used for a different request body."""


@dataclass
class IdempotencyMiss:
    pass


@dataclass
class IdempotencyHit:
    response: dict
    status_code: int
    created_at: datetime


def fingerprint_request(body: dict) -> str:
    """Stable SHA-256 fingerprint of a request body (key-order independent)."""
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class IdempotencyStore(Protocol):
    def lock_for(self, *, key: str, endpoint: str) -> asyncio.Lock: ...

    async def lookup(self, *, key: str, endpoint: str, body: dict) -> IdempotencyHit | IdempotencyMiss: ...

    async def store(self, *, key: str, endpoint: str, body: dict, response: dict, status_code: int) -> None: ...


class SqlIdempotencyStore:
    """SQL-backed store; survives process restarts.

    The schema is intentionally tiny: a single keyed row per (key, endpoint).
    Concurrent first-writers race on the unique index; whoever loses simply
    treats the row as already-existing and lets the lookup replay it. This is
    exactly the at-least-once + idempotent contract we promise to callers.
    """

    def __init__(self, session_factory: Any, *, ttl: timedelta = timedelta(hours=24)) -> None:
        self._sf = session_factory
        self._ttl = ttl
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def lock_for(self, *, key: str, endpoint: str) -> asyncio.Lock:
        # ponytail: process-local serialization matches the documented single-Gateway deployment;
        # replace with a database claim before enabling multiple workers.
        return self._locks.setdefault((key, endpoint), asyncio.Lock())

    async def lookup(self, *, key: str, endpoint: str, body: dict) -> IdempotencyHit | IdempotencyMiss:
        from sqlalchemy import select

        from .idempotency_orm import IdempotencyRecordRow

        async with self._sf() as session:
            row = await session.scalar(select(IdempotencyRecordRow).where(IdempotencyRecordRow.key == key, IdempotencyRecordRow.endpoint == endpoint))
            if row is None:
                return IdempotencyMiss()
            expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
            if datetime.now(UTC) >= expires_at:
                await session.delete(row)
                await session.commit()
                return IdempotencyMiss()
            if row.request_fingerprint != fingerprint_request(body):
                raise IdempotencyConflict(f"Idempotency-Key {key!r} was previously used for a different request body on {endpoint}")
            return IdempotencyHit(response=row.response_json, status_code=row.status_code, created_at=row.created_at)

    async def store(self, *, key: str, endpoint: str, body: dict, response: dict, status_code: int) -> None:
        from sqlalchemy.exc import IntegrityError

        from .idempotency_orm import IdempotencyRecordRow

        now = datetime.now(UTC)
        async with self._sf() as session:
            row = IdempotencyRecordRow(
                key=key,
                endpoint=endpoint,
                request_fingerprint=fingerprint_request(body),
                response_json=response,
                status_code=status_code,
                created_at=now,
                expires_at=now + self._ttl,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # Another writer committed first; we keep the canonical record.
                await session.rollback()


class InMemoryIdempotencyStore:
    """Async-safe in-process store; the canonical impl lives in tests.

    The production code path persists records to SQL (see `SqlIdempotencyStore`
    in `repository.py`) so retries survive process restarts. This in-memory
    variant exists so the contract is testable without a database.
    """

    def __init__(self, *, ttl: timedelta = timedelta(hours=24)) -> None:
        self._records: dict[tuple[str, str], dict] = {}
        self._ttl = ttl
        self._lock = asyncio.Lock()
        self._run_locks: dict[tuple[str, str], asyncio.Lock] = {}

    def lock_for(self, *, key: str, endpoint: str) -> asyncio.Lock:
        return self._run_locks.setdefault((key, endpoint), asyncio.Lock())

    async def lookup(self, *, key: str, endpoint: str, body: dict) -> IdempotencyHit | IdempotencyMiss:
        record = self._records.get((key, endpoint))
        if record is None:
            return IdempotencyMiss()
        expires_at = datetime.fromisoformat(record["expires_at"])
        if datetime.now(UTC) >= expires_at:
            # Expired: forget it and treat as a fresh request. We do NOT raise
            # here — the caller wanted idempotency, not eternal memory.
            self._records.pop((key, endpoint), None)
            return IdempotencyMiss()
        if record["request_fingerprint"] != fingerprint_request(body):
            raise IdempotencyConflict(f"Idempotency-Key {key!r} was previously used for a different request body on {endpoint}")
        return IdempotencyHit(
            response=record["response"],
            status_code=record["status_code"],
            created_at=datetime.fromisoformat(record["created_at"]),
        )

    async def store(self, *, key: str, endpoint: str, body: dict, response: dict, status_code: int) -> None:
        now = datetime.now(UTC)
        async with self._lock:
            existing = self._records.get((key, endpoint))
            if existing is not None:
                # Two concurrent writes with the same key: the first writer's
                # response is the canonical one. The second writer's response
                # is dropped. The caller can detect this by re-running the
                # operation and observing the cached record.
                return
            self._records[(key, endpoint)] = {
                "request_fingerprint": fingerprint_request(body),
                "response": response,
                "status_code": status_code,
                "created_at": now.isoformat(),
                "expires_at": (now + self._ttl).isoformat(),
            }


# --- FastAPI integration helper ---------------------------------------------


@dataclass
class IdempotencyOutcome:
    """What the FastAPI handler should do next."""

    replay: bool
    cached_response: dict | None = None
    cached_status: int | None = None


async def apply_idempotency(
    store: IdempotencyStore,
    *,
    key: str | None,
    endpoint: str,
    body: dict,
) -> IdempotencyOutcome:
    """Apply the idempotency contract before the handler runs.

    No key → always miss (caller opted out, or endpoint is read-only).
    Hit → caller should return the cached response.
    Conflict → caller should raise 422.
    """
    if key is None or key == "":
        return IdempotencyOutcome(replay=False)
    result = await store.lookup(key=key, endpoint=endpoint, body=body)
    if isinstance(result, IdempotencyHit):
        return IdempotencyOutcome(replay=True, cached_response=result.response, cached_status=result.status_code)
    return IdempotencyOutcome(replay=False)
