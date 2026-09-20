# ADR-002: Idempotency-Key Across All AfterFlow Write Endpoints

## Status

Accepted (P0-2 of the engineering maturity roadmap).

## Context

The pre-P0 codebase had *business* idempotency for refund execution:
`idempotency_key = sha256(case_id:refund:payload_hash)` and a DB UNIQUE
constraint. This protected against the *server* double-running an
action. It did NOT protect against the *client* double-submitting a
network call that the server already processed and replied to — the
client never got the reply (timeout, dropped connection, browser
refresh), so it retried, and the server now ran again.

Three further gaps:

- `POST /api/after-sales/cases` had no idempotency. A double-click
  created two cases.
- `approve / reject` had version-based optimistic locking but no
  retry semantics; a retry with the same body after the version moved
  produced a 409.
- `MockReverseExecutor.dispatch` deduplicated by an internal key not
  visible to the caller.

## Decision

Adopt the Stripe-style `Idempotency-Key` header on every write endpoint:

- `app/after_sales/idempotency.py` defines:
  - `IdempotencyStore` Protocol
  - `InMemoryIdempotencyStore` for tests
  - `SqlIdempotencyStore` for production (table
    `after_sales_idempotency`, unique on `(key, endpoint)`)
  - `apply_idempotency(store, *, key, endpoint, body)` returns
    `IdempotencyOutcome` (replay / no-op)
  - `IdempotencyConflict` on key + different body (caller bug, 422)

- Migration `0006_after_sales_idempotency` adds the table.

- The router exposes the Idempotency-Key header on:
  - `POST /api/after-sales/cases`
  - `POST /api/after-sales/actions/{id}/approve`
  - `POST /api/after-sales/actions/{id}/reject`
  - `POST /api/after-sales/actions/{id}/execute`

- Reused key + same body → cached response replayed.
- Reused key + different body → 422 `IdempotencyConflict`.
- No key → behaviour unchanged (caller opts out).

The pre-existing business idempotency on `action_requests.idempotency_key`
remains: it is the *server-side* defence against a server bug causing
double execution. The Idempotency-Key header is the *client-side*
defence against a network glitch causing a double submission. Both
must exist; they protect against different failures.

## Consequences

- The client SDK can safely retry on connection failure: it just
  keeps the same `Idempotency-Key` and replays the previous response.
- A caller reusing a key with a different body is a programming bug
  — 422 surfaces it loudly instead of silently overwriting.
- The 24-hour TTL matches Stripe's recommendation; production may
  want to tune it via config.

## Why not

- "Just rely on optimistic locking / version" — version locks the
  same action, but a retried `POST /cases` from the client is a
  *different* action with the same intent. The header captures intent.
- "Use Redis" — for a single-process demo, SQL is sufficient and
  avoids a new dependency. The store is behind a Protocol so a
  Redis-backed implementation is a drop-in.

## Future Evolution

- Multi-process deployments: replace `SqlIdempotencyStore` with a
  Redis-backed store behind the same Protocol.
- Admin endpoints: idempotency on `cancel_case`, `reassign_action`,
  and other state-machine transitions.