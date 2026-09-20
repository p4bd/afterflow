# ADR-008: P0 Production-Wiring Upgrade

## Status

Accepted (2026-09-03). Closes the four outstanding P0 items from the
engineering-maturity audit report.

## Context

The earlier engineering-maturity audit (`docs/ARCHITECTURE.md` §10 +
ADR-001..007) flagged four high-priority gaps that prevented several
defensive layers from actually running in production:

1. Five defensive modules — `SqlIdempotencyStore`,
   `AuditedAfterSalesGuardrailProvider`, `ToolPolicyStore`,
   `CancellationRegistry`, `SqlKnowledgeHealthRecorder` — were
   implemented and tested, but the production lifespan never
   instantiated them. The audit called this out as "5 个新模块
   （…）未生产接线".
2. `append_event` did not enforce uniqueness on `(case_id, seq)`. Two
   concurrent calls could both compute the same `seq` and INSERT,
   forking the audit hash chain. The auditor's
   `verify_event_chain` walks by `seq` and either branch validates
   locally, but the other becomes orphaned — a real tamper-evidence
   break.
3. The migrations directory had parallel branches
   (`0006_action_approvers` + `0006_after_sales_idempotency`,
   `0007_case_event_hash_chain` + `0007_tool_call_audit`) with no
   merge. Multiple heads made `alembic upgrade head` ambiguous in
   cross-branch scenarios.
4. The frontend never sent the `Idempotency-Key` header that the
   backend's `_idem_replay_or_run` helper expects. The Stripe-style
   contract was unreachable from the UI even though the backend was
   ready.

## Decision

### 1. P0-A: Wire five defensive modules into the lifespan

`backend/app/gateway/deps.py::langgraph_runtime` now binds:

| Module | Bound to | Type |
|---|---|---|
| `SqlIdempotencyStore` | `app.state.after_sales_idempotency_store` | SQL |
| `AuditedAfterSalesGuardrailProvider` (wrapping the original) | `app.state.after_sales_guardrail_provider` | SQL-audited |
| `ToolPolicyStore()` | `app.state.after_sales_tool_policy_store` | In-memory (per-process, per-session) |
| `CancellationRegistry()` | `app.state.after_sales_cancellation_registry` | In-process |
| `SqlKnowledgeHealthRecorder` | `app.state.after_sales_knowledge_health_recorder` + module-level `_health_recorder` (via `set_knowledge_health_recorder`) | SQL |

When `get_session_factory()` is None (the in-memory demo mode), the
lifespan falls back to in-memory implementations so the demo still
runs. The `_json_serializer` in `packages/.../engine.py` was extended
to handle `datetime`/`date` so the SQL store does not crash on
serializing case/event rows that carry timezone-aware timestamps.

`config.example.yaml` now references the audited provider explicitly.

A new test file `backend/tests/test_after_sales_lifespan_wiring.py`
boots the FastAPI app through the lifespan and asserts that all five
`app.state.*` attributes are set to the correct concrete types. The
eighth test is a full router roundtrip with `Idempotency-Key` header
that proves the SQL store replays correctly through the lifespan path.

### 2. P0-B: Fix `append_event` concurrent hash-chain forking

Two changes:

- `CaseEventRow.__table_args__` now includes
  `UniqueConstraint("case_id", "seq", name="uq_case_events_case_id_seq")`.
  The pre-existing global `UNIQUE(seq)` column constraint was removed
  because it was an artifact of the autoincrement design and would
  forbid two cases from both having an event with `seq=1`.
- `repository.py::append_event` wraps the INSERT in a bounded
  three-attempt retry that re-reads `last` inside its own transaction
  on `IntegrityError`. After exhausting retries, raises
  `ConcurrentActionError`.

Migration `0011_case_event_seq_unique.py` (new) adds the composite
UNIQUE INDEX in a way that is idempotent against the existing schema
(drops the legacy `ix_case_events_seq` unique index and recreates it
as a non-unique index for fast `seq` scans).

### 3. P0-D: Merge parallel migration branches

Each of the previously unlabelled parallel branches now carries an
explicit `branch_labels` string:

- `0006_action_approvers`: `branch_labels = "after_sales_approvers"`
- `0006_after_sales_idempotency`: `branch_labels = "after_sales_idempotency"`
- `0007_case_event_hash_chain`: `branch_labels = "after_sales_hash_chain"`
- `0007_tool_call_audit`: `branch_labels = "after_sales_tool_audit"`

`0008_action_reserved` declares `depends_on = "0007_tool_call_audit"`
so it waits for the tool-audit branch to complete (without changing
its committed `down_revision`).

Migration `0011_case_event_seq_unique` uses
`down_revision = ("0010_after_sales_knowledge_health", "0007_tool_call_audit")`
as a tuple, making it a true merge migration that collapses both
heads into a single linear chain. `alembic heads` now returns a
single head.

### 4. P0-C: Frontend sends `Idempotency-Key`

`frontend/src/core/after-sales.ts` now exports a `generateIdempotencyKey()`
helper that uses `crypto.randomUUID()` when available (with a manual
UUIDv4 fallback for SSR). The three POST functions
(`approveAfterSalesAction`, `rejectAfterSalesAction`,
`executeAfterSalesAction`) accept an optional `idempotencyKey` parameter
that defaults to a fresh key per call. Each call sends the
`Idempotency-Key` header. No memoization across calls — a double-click
gets two distinct keys, and the backend's `expected_version`
optimistic-lock catches the duplicate as it should.

Four new unit tests pin the helper's UUIDv4 layout, length,
uniqueness across 64 calls, and non-equality between consecutive
calls. The full frontend suite stays green at 532 passed / 0 failed.

## Consequences

- The five defensive modules now run in production. The audit-report
  gap "未生产接线" is closed for `SqlIdempotencyStore`,
  `AuditedAfterSalesGuardrailProvider`, `ToolPolicyStore`,
  `CancellationRegistry`, and `SqlKnowledgeHealthRecorder`.
- Two concurrent `append_event` calls can no longer fork the audit
  chain. The unique constraint + retry makes the chain strictly
  monotonic per case, with at most 3 retries before failing loudly.
- The migration graph is a single linear chain. `alembic upgrade head`
  is unambiguous.
- The Stripe-style Idempotency-Key contract is reachable from the UI.
  Network-retry safety on the three money-moving endpoints is no
  longer an empty promise.

## Why not

- **Redis-backed CancellationRegistry** — kept the in-process registry
  per the documented contract. A multi-worker deployment would swap
  for a Redis-backed store behind the same Protocol; the lifespan
  wiring makes that swap a one-line change.
- **Wire `ToolPolicyStore` into the agent middleware** — deferred.
  The store is now bound on `app.state` and tests prove the budget
  raises correctly, but no middleware currently calls
  `record_step()` per loop iteration. The integration is a small
  follow-up.

## Future Evolution

- Hook `record_step()` into `LoopDetectionMiddleware` so agent runs
  actually consume the step budget instead of just having one
  available.
- Wire `raise_if_cancelled(registry, run_id)` into the long-running
  domain calls (`evaluate_mock_case`, `_build_context`).
- Consider promoting the unique-constraint enforcement up into
  `verify_event_chain` itself so a forked chain is detected at read
  time, not just at write time.