# ADR-004: Tool-call Audit Log

## Status

Accepted (P0-4 of the engineering maturity roadmap).

## Context

The pre-P0 codebase recorded `case_events` (decision_generated,
approval_requested, approval_granted, execution_succeeded) with a
hash chain. This answered "what happened to the case" but NOT
"what the agent tried to do on the way there". An auditor reading
the event chain could see "execution_succeeded with txn XYZ" but
could not answer:

- Which Tools did the agent call to get here?
- What arguments did the agent pass?
- Did the Guardrail allow or deny each call?
- What was the latency of each call?
- Were any calls denied for "subagent_forbidden" or
  "permission_denied"?

## Decision

Add `app/after_sales/tool_audit.py` + `tool_audit_orm.py`:

- `ToolCallAudit` records: case_id, actor, user_role, tool_name,
  params_hash (NOT raw args — privacy), decision (ALLOW/DENY),
  reason_code, latency_ms, metadata.
- `ToolCallAuditor` Protocol with `record()` and `list_for_case()`.
- `InMemoryToolCallAuditor` for tests.
- `SqlToolCallAuditor` for production (table
  `after_sales_tool_audit`, indexed by `(case_id, recorded_at)`).
- `AuditedAfterSalesGuardrailProvider` wraps the existing
  `AfterSalesGuardrailProvider` and emits an audit row for every
  decision (allow or deny), measuring latency end-to-end.
- Migration `0007_tool_call_audit` adds the table.

`fingerprint_params()` is the privacy-preserving hash used for
`params_hash` — raw arguments may carry PII or secrets, but the
fingerprint proves "this exact input was called twice" without
exposing values.

## Consequences

- Auditors can pull the full call chain for a case in O(rows).
- `deny` rows carry the reason code, so investigators can see WHY
  the agent tried to call `create_after_sales_action` and was
  refused.
- The Guardrail wraps (does not modify) the original provider, so
  callers without an auditor get the same behaviour as before.

## Why not

- "Audit via OpenTelemetry spans" — out of scope for this project;
  the audit row is a SQL query away. Spans can be added later.
- "Persist raw params" — privacy + storage cost. The hash is enough.

## Future Evolution

- Add `tool_call_audit` to the existing `verify_event_chain` so a
  tampered audit row breaks the hash chain.
- Expose the audit list as an admin endpoint for human review.