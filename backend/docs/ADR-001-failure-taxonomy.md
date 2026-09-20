# ADR-001: AfterFlow Failure Taxonomy and Recovery Policy

## Status

Accepted (P0-1 of the engineering maturity roadmap).

## Context

The pre-P0 codebase had four internal exception types
(`ActionForbidden / ActionConflict / ValueError / ConcurrentActionError`)
and `try/except (..., ValueError) → return _json({"error": ...})` patterns
across every Tool. Two failure modes were common:

1. Silently swallowing unknown exceptions — `try: ... except Exception:
   return _mock_knowledge()` in `knowledge.py`.
2. Retrying things that must never be retried (e.g. a permission denial,
   which would otherwise become a privilege-escalation probe).

The agent loop and the HTTP layer had no shared classification: the same
exception could be interpreted as "retry" in one place and "fail" in
another. Auditors couldn't answer "did the system retry this action?".

## Decision

Add `app/after_sales/failure_taxonomy.py`:

- `FailureClass` is a closed `StrEnum`. Adding a new value forces the
  developer to also add a row to `_DECISION_TABLE`.
- `Decision` is the closed set of recovery actions:
  `RETRY | RETRY_WITH_BACKOFF | ASK_USER | HUMAN_REVIEW | FALLBACK |
   REJECT | FAIL | FAIL_CLOSED`.
- `decision_for(class)` returns the recovery action.
- `is_safe_to_retry(class)` returns the boolean retryable flag — the
  only function a retry decorator / HTTP middleware is allowed to call.
- `classify_error(exc)` maps Python exceptions to a class.

A contract test (`test_failure_class_is_closed_enum`) pins the closed
shape: any new class added must be intentional.

## Consequences

- HTTP middleware / Tool wrappers consult `decision_for()` instead of
  inventing their own retry policy.
- `FAIL_CLOSED` for `PERMISSION_ERROR` makes retry-as-privilege-probe
  impossible: a wrapped retry decorator must call `is_safe_to_retry()`
  before retrying.
- `MODEL_ERROR → HUMAN_REVIEW`: model outputs are not auto-retried by
  the same input; a human must inspect.
- `EXTERNAL_DEPENDENCY_UNAVAILABLE → FALLBACK`: the RAG/MCP layer can
  fail open without breaking money decisions (paired with P0-5 health
  recorder).

## Why not

- "Just use HTTP status codes" — too coarse for an internal failure
  model; e.g. a 409 might be a TOCTOU conflict (FAIL) or a policy
  rejection (REJECT).
- "Use a third-party resilience library" — the contract here is
  policy-driven, not generic retry/backoff, and we want the table to
  be a single readable file.

## Future Evolution

When AfterFlow gains additional decision domains (e.g. fraud scoring,
dispute mediation), the same taxonomy must apply. New failure classes
get added; the contract test enforces that they are bound to a
recovery decision in `_DECISION_TABLE`.