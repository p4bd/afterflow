# ADR-009: Prometheus /metrics endpoint (P1-A Observability)

## Status

Accepted (2026-09-03). Closes the last P1 item from the engineering-maturity
audit (`docs/ARCHITECTURE.md` §10).

## Context

The earlier engineering-maturity audit (§10 of `docs/ARCHITECTURE.md`) flagged
four high-priority items plus one medium-priority item. ADR-008 closed the
four P0 items; the remaining gap was:

> P1-A — Observability. There is no Prometheus /metrics endpoint, no
> decision counter, no execution counter. Operators cannot answer
> "how many refunds were approved in the last hour?" without parsing
> audit-chain JSON.

Without a metrics surface, the platform cannot satisfy the most basic SLO
question — *is the system doing what we think it is doing?* — without
falling back to grep-on-logs or querying the audit chain directly. Both
options are too slow for an on-call dashboard.

The straightforward fix is to add a Prometheus-format `/metrics` endpoint
with real counters at every after-sales decision boundary.

## Decision

### 1. Hand-rolled text format — no new dependency

The codebase does not currently depend on `prometheus_client`. We
deliberately do NOT add it. The text exposition format is tiny:

```
# HELP <metric_name> <docstring>
# TYPE <metric_name> counter
<metric_name>{<label>="<value>", ...} <number>
```

… and our surface is seven counter families. Adding `prometheus_client`
would pull in:

- ~12 transitive dependencies (`twisted`, `pyparsing`, …) that need a
  separate security review
- A threading model (`ValueClass`, `MultiProcessValue`) we do not need —
  the Gateway runs on a single worker per process by default
- A global `default_registry` that complicates test isolation (each
  test would have to call `CollectorRegistry()` setup) and increases
  the cost of the cross-import graph

`backend/app/after_sales/metrics.py` implements the format in ~180 lines.
The format we emit is validated line-by-line in
`tests/test_after_sales_metrics.py::test_render_is_spec_compliant` and
conforms to
<https://prometheus.io/docs/instrumenting/exposition_formats/>.

### 2. Metric surface

Seven counter families, each with HELP + TYPE + sample lines:

| Family | Labels | Where it fires |
|---|---|---|
| `decisions_total` | `outcome={eligible,eligible_with_approval,ineligible,needs_evidence}` | `gateway/routers/after_sales.py::create_case` after the deterministic decision is computed |
| `actions_created_total` | `status={pending_approval,approved}` | `create_action` after the action is persisted (auto-approved fires `status=approved`) |
| `approvals_total` | `decision={granted,rejected}` | `approve` and `reject` routes, only on success |
| `executions_total` | `outcome={succeeded}` | `execute` route, only on success (failures are visible in access logs) |
| `idempotency_total` | `result={hit,miss,conflict}` | `_idem_replay_or_run` helper, at every entry point |
| `tool_audit_total` | `decision={allow,deny}` | `AuditedAfterSalesGuardrailProvider.aevaluate` after the inner decide |
| `reservation_reaped_total` | (none) | `list_actions` route, once per released reservation |

The label set is intentionally narrow. We chose to expose only
**business-meaningful** counters — not every Redis hit or DB query. Adding
more families later is a one-line change in `_METRIC_HELP` /
`_METRIC_LABELS`.

### 3. `/metrics` HTTP endpoint

`backend/app/gateway/routers/metrics.py` mounts a single
`GET /metrics` route. It returns
`PlainTextResponse(get_metrics().render())` with `Content-Type:
text/plain; charset=utf-8`. The route has no auth — internal scrapers
(Prometheus server, Grafana Agent) hit it from a trusted network only;
the auth path is at the network layer (Nginx / k8s NetworkPolicy).

### 4. Wiring

- **`backend/app/gateway/routers/after_sales.py`**: counter increments at
  every decision boundary listed above. The `_idem_replay_or_run` helper
  bumps the idempotency counter in three branches (hit / miss / conflict)
  so all four write endpoints (create case, approve, reject, execute)
  share a single observability funnel.
- **`backend/app/after_sales/guardrail.py`**: the `AuditedAfterSalesGuardrailProvider`
  — already the single source of truth for allow/deny at runtime — emits
  `tool_audit_total` immediately after the inner `aevaluate` decides.
- **`backend/app/gateway/app.py`**: `app.include_router(metrics.router)`
  after the existing `after_sales` router include.

### 5. Threading

The `Metrics` dataclass is guarded by a single `threading.Lock`. `inc()`
takes the lock once, `render()` snapshots inside the lock and releases
before string concatenation. The lock is held for microseconds — no
concern for a request-per-millisecond load.

## Consequences

### Positive

- One new HTTP endpoint, ~20 lines of FastAPI code, zero new runtime deps.
- Every counter has a real production-code call site (no synthetic data
  to keep "fresh").
- The format is spec-validated; a Prometheus server will scrape it
  without complaint.
- Tests are deterministic: `reset_metrics()` zeroes the singleton between
  cases, the existing `test_after_sales_metrics.py` proves the counters
  move at every wiring point.

### Negative

- We hand-rolled a small piece of the Prometheus spec. Future
  metric types (gauge, histogram, summary) will need new code; the
  current `inc()` API does not handle them. This is acceptable for a
  counter-only first cut.
- The `Metrics` singleton is per-process. Multi-worker deployments
  (GATEWAY_WORKERS > 1) will report per-worker counts that sum only at
  scrape time. For the counter families here (decisions / approvals /
  executions per worker) this is fine because Prometheus naturally
  aggregates. For `reservation_reaped_total` it can under-count if the
  reaper only runs in one process; see Future Evolution.
- Auth is intentionally off. We rely on the network layer to keep
  `/metrics` off the public internet. If this changes, add a basic-auth
  filter at the Nginx layer rather than in FastAPI.

## Why not `prometheus_client`?

| Concern | `prometheus_client` | Hand-rolled |
|---|---|---|
| New dep | yes, ~12 transitive | none |
| Lines of code we own | ~0 (delegated) | ~180 |
| Spec risk | upstream tracks spec | we own it |
| Test isolation | needs custom `CollectorRegistry` | `reset_metrics()` |
| Multi-process | built-in mmap multiprocess mode | out of scope (see above) |
| Histogram / summary | built-in | we'd add it later |
| Latency in hot path | dict-of-dicts lookup, similar | dict-of-dicts lookup, similar |

For our surface (7 counter families, no histograms) the maintenance cost
of hand-rolling is lower than the dependency-review cost of
`prometheus_client`. When a second metric type appears (histogram for
guardrail decision latency, gauge for in-flight run count), this decision
should be revisited.

## Future Evolution

1. **Add `prometheus_client` when we need histogram/summary** — likely
   the first histogram is `guardrail_decision_latency_seconds` (replace
   the current `latency_ms` field on the audit row).
2. **Wire a `Gauge` for in-flight runs** so operators can see queue
   depth at a glance. Requires a small `runtime.runs.metrics` module.
3. **Scrape config + Grafana dashboard** ship alongside the `make dev`
   stack: a Prometheus config snippet in `docker/` plus a JSON dashboard
   in `docs/grafana/` that plots each family.
4. **Multi-process aggregation**: when `GATEWAY_WORKERS > 1`, switch
   to `prometheus_client`'s multiprocess mode (the only reason to
   add the dependency retroactively). The `/metrics` endpoint would
   then aggregate across workers via the `PROMETHEUS_MULTIPROC_DIR`
   shared memory directory.
5. **Alerting rules**: turn `approvals_total{decision="rejected"} / approvals_total`
   into a "rejection spike" alert; turn `idempotency_total{result="conflict"} > 0`
   into a "caller bug" alert.