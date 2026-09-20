# ADR-003: Approval-time Balance Re-check

## Status

Accepted (P0-3 of the engineering maturity roadmap).

## Context

The pre-P0 codebase validated balance only at *execute* time, against
the reserved pool. The reservation was created at *approve* time. This
left a window of up to 24 hours (action TTL) where:

- A second action on the same order could drain the available pool,
- The first action could approve without realising the second had
  already taken its share,
- Execution would then fail with a confusing 409.

For a money-moving system, "approve first, fail later at execute" is
the wrong direction: it makes the reviewer think they've signed off
on something the system knows will fail.

## Decision

Add `app/after_sales/balance_check.py`:

- `BalanceCheckFailed(ActionConflict)` is raised when the executor's
  `balance_of(order_id) < action.payload["amount"]`.
- `approve_action()` accepts an optional `balance_check` callable.
  When supplied, it runs *before* the action is flipped to APPROVED.
- The router's approve handler wires the executor's balance check.

## Consequences

- A drained pool produces a clear 409 at approve time: "insufficient
  available balance".
- Reviewers never sign off on a doomed approval.
- The balance check is opt-in via parameter so unit tests that don't
  care about the executor can still drive the state machine.

## Why not

- "Read fresh balance in the Guardrail" — Guardrail runs on tool
  call, but approval is a *HTTP endpoint*, not a tool call.
- "Move the reservation to approve time only" — that is what we
  already do. The check guards against the gap *between* approve and
  the reservation.

## Future Evolution

When AfterFlow integrates a real payment gateway, the
`check_balance_before_approve` callable can be backed by a fresh
gateway query (e.g. `GET /balance?order_id=...`) rather than an
in-memory executor. The contract stays the same.