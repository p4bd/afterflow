# ADR-005: Knowledge Layer Health Recorder

## Status

Accepted (P0-5 of the engineering maturity roadmap).

## Context

The pre-P0 `retrieve_knowledge()` function had fail-open semantics:
on any exception, it returned a deterministic mock policy string.
This kept the workflow running, but it was *silent* — if the MCP
server went down for hours, business would not know, and the demo
would still pass tests using the mock fallback.

This is exactly the failure mode that quietly degrades an AI
product: the answers get worse, no alarm fires, no metric surfaces.

## Decision

Add `app/after_sales/knowledge_health.py`:

- `KnowledgeHealthRecorder` Protocol.
- `InMemoryKnowledgeHealthRecorder` for tests and process-local
  production.
- `record_attempt(recorder, *, query, fn)` runs `fn()` and records
  success or failure (with reason). On exception, returns None
  (the caller's fallback signal).
- Health snapshot exposes:
  - `healthy`: True while `consecutive_failures < unhealthy_threshold`
  - `attempt_count`, `fallback_count`
  - `consecutive_failures`
  - `last_error`, `last_attempt_at`

`retrieve_knowledge()` now wraps `client.search()` through
`record_attempt()` and bumps the counter on every fallback.

A new Tool `get_knowledge_health` exposes the snapshot to operators
(visible in the agent's tool list and the approval UI).

## Consequences

- A persistent MCP outage flips `healthy → False` after 3 consecutive
  failures; an operator alert can be wired off this flag.
- The `record_attempt` helper keeps the fail-open contract — money
  decisions still run, but the degradation is visible.
- Tests pin the threshold-based unhealthy transition.

## Why not

- "Just count errors in OpenTelemetry" — same reasoning as ADR-004.
- "Always raise instead of fallback" — would break the
  inform-layer-vs-validate-layer separation; P0-5 keeps the
  separation intact but adds observability.

## Future Evolution

- SQL-backed recorder so the snapshot survives process restart.
- Prometheus counter `afterflow_knowledge_fallback_total` exported
  to a `/metrics` endpoint.