# ADR-006: Per-tool Throttle and Step Budget

## Status

Accepted (P0-6 of the engineering maturity roadmap).

## Context

The pre-P0 agent loop had no defensive limits:

- A bad prompt could make the agent call `get_logistics_evidence` 50
  times in a session before producing a final answer.
- There was no max_steps ceiling; only the LangChain run-time's
  internal max_iterations limit (which is generous).
- The agent middleware had `ToolErrorHandlingMiddleware` and
  `DanglingToolCallMiddleware` but no rate-limiter.

For an LLM that may loop on a missing field or misunderstand a
response, these are runaway-loop failure modes — easy to hit, hard
to detect after the fact.

## Decision

Add `app/after_sales/tool_policy.py`:

- `ToolPolicy` config object with `max_steps=25`,
  `max_calls_per_session=8`, plus `per_tool_overrides`.
- `DEFAULT_PER_TOOL_LIMITS` sets per-tool caps:
  - Read tools: 2-4 calls max
  - Decision tools: 4 calls max
  - Write tools: 1 call (one-shot, guarded by Guardrail separately)
- `ToolPolicyStore` session-scoped:
  - `check_and_record(tool_name, args)` atomically increments the
    counter and returns a `ToolCallThrottle(allowed, used, cap, tool_name)`.
  - `record_step()` increments the step counter and raises
    `StepBudgetExceeded` when over budget.
- Throttle verdicts are exposed as Tool return values (NOT silent
  drops), so the agent sees that it has hit a wall.

These limits are defensive: they catch the runaway, they do NOT
constrain normal problem-solving.

## Consequences

- A misbehaving agent stops wasting tokens after 25 steps.
- A misbehaving agent stops calling the same read tool 50 times.
- The agent middleware sees the `ToolCallThrottle` and can return
  a clear error to the model.
- Tests pin the cap semantics, the independence of per-tool
  counters, and the budget raise.

## Why not

- "Use a third-party rate limiter" — the policy is domain-specific
  (different caps per tool), so a generic token-bucket doesn't fit.
- "Hard-stop the agent loop" — that breaks mid-flow; the agent
  should know it hit a wall and respond accordingly.

## Future Evolution

- Per-user (not per-session) caps when AfterFlow is multi-tenant.
- Cost-based caps (USD) instead of call-count caps for production.