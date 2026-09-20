# ADR-007: Cancellation Propagation Contract

## Status

Accepted (P0-7 of the engineering maturity roadmap).

## Context

The pre-P0 codebase had no explicit cancellation contract. A client
that disconnected mid-stream left:

- The case evaluation graph half-run.
- The mock executor in a partial state.
- No event in the case audit chain recording "the user closed the
  browser tab".

This made it hard to answer "did the user's disconnect cancel the
refund, or did it succeed silently?" Two equally bad failure modes:
a silently-completed refund the user never saw, or a partially-cancelled
one that left money reserved but unexecuted.

## Decision

Add `app/after_sales/cancellation.py`:

- `CancellationRegistry` is a process-local registry mapping
  `run_id → (reason, cancelled_at)`.
- `cancel(run_id, reason)` marks the run as cancelled.
- `is_cancelled(run_id)` returns the flag.
- `raise_if_cancelled(registry, run_id)` raises `Cancelled` at the
  next await point inside a long-running workflow.

The contract is:

1. **Cancel = stop spending CPU.** It does NOT roll back side effects
   that already committed. A reserved refund stays reserved; a
   completed refund stays completed.
2. **A pending approval that the user abandoned stays in
   `pending_approval`** until it expires (24h TTL). It is NOT
   auto-rejected on cancel — that would let a flaky network
   double-reject a refund.
3. **A cancellation event is recorded on the case** so an operator
   can see "this case was abandoned at 14:32".
4. **Cancellation races the agent loop.** If the agent has already
   produced a side effect (refund reserved), the cancellation does
   NOT undo it.

The registry is in-process for the demo. A production multi-process
deployment would swap it for a Redis-backed store behind the same
Protocol.

## Consequences

- The HTTP middleware can call `registry.cancel(run_id)` on
  `request.is_disconnected()`.
- Long-running domain functions (`evaluate_mock_case`,
  `_build_context` in the workflow) can sprinkle
  `raise_if_cancelled()` at natural await points.
- Idempotent retries after cancel still see the original side effect;
  no double-spending risk.

## Why not

- "Use asyncio.CancelledError directly" — that's a task-local
  primitive; AfterFlow needs a run-scoped registry that survives
  task boundaries.
- "Roll back on cancel" — impossible without a transaction log
  (see ADR-008 for the rejection of Event Sourcing). Rollback is
  done by the reaper + 24h TTL, not by the cancel handler.

## Future Evolution

- A dedicated `Cancelled` case event type for the audit chain.
- A scheduled reaper that, for cases cancelled-but-not-resolved
  past TTL, auto-releases reservations and records the cleanup.