"""Hand-rolled Prometheus counters for AfterFlow Observability.

The P0 production-wiring upgrade closed the lifespan gap; this module closes
the Observability gap by adding a Prometheus-format ``/metrics`` endpoint
with real counters at every after-sales decision boundary.

We deliberately do NOT depend on ``prometheus_client``. The text exposition
format is tiny (HELP/TYPE + ``name value`` lines), the metrics surface we
need is tiny, and adding a third-party package for ~80 lines of code would
introduce a security review, a transitive dependency graph, and a
threading model we don't need. See ``docs/ADR-009-metrics-endpoint.md``
for the full rationale.

Format we emit:
    # HELP <name> <description>
    # TYPE <name> counter
    <name>{<label>="<value>", ...} <number>
    <name> <number>

Label values are escaped using the spec rules: backslash, double quote, and
newline are escaped. We do not currently emit UTF-8 label values, but the
escape function is defensive against future callers that might.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import Lock

# ---------------------------------------------------------------------------
# Metric families — single source of truth for HELP text + label keys
# ---------------------------------------------------------------------------

#: HELP text per metric family. Keep these lines under 80 chars where
#: practical so the text exposition stays scannable.
_METRIC_HELP: dict[str, str] = {
    "decisions_total": "Total decisions made by the engine, by eligibility outcome.",
    "actions_created_total": "Total refund actions created, by initial status.",
    "approvals_total": "Total approval / rejection decisions made by supervisors.",
    "executions_total": "Total approved-action executions, by terminal outcome.",
    "idempotency_total": "Idempotency-Key lookups by result (hit=replay, miss=first-write, conflict=key reused with different body).",
    "tool_audit_total": "Guardrail decisions on AfterFlow tool calls, by allow/deny.",
    "reservation_reaped_total": "Expired reserved actions released by the reservation reaper.",
}

#: Sorted list of label keys per metric family. Empty tuple = label-less
#: counter. The order here is the order rendered, which keeps the scrape
#: diff-friendly in PRs.
_METRIC_LABELS: dict[str, tuple[str, ...]] = {
    "decisions_total": ("outcome",),
    "actions_created_total": ("status",),
    "approvals_total": ("decision",),
    "executions_total": ("outcome",),
    "idempotency_total": ("result",),
    "tool_audit_total": ("decision",),
    "reservation_reaped_total": (),
}


def _escape_label_value(value: str) -> str:
    """Apply the Prometheus label-value escape rules.

    Backslash, double quote, and line feed become ``\\\\``, ``\\"``, ``\\n``
    respectively. We do not need full Unicode handling today but keep the
    hook for callers that might.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    # Stable ordering: sort by key. Prometheus does not require this, but a
    # deterministic order makes the scrape diff-friendly across requests.
    parts = [f'{key}="{_escape_label_value(labels[key])}"' for key in sorted(labels)]
    return "{" + ",".join(parts) + "}"


# ---------------------------------------------------------------------------
# Metrics container
# ---------------------------------------------------------------------------


@dataclass
class Metrics:
    """In-memory counter registry.

    Each metric family is a nested dict of ``{label_tuple: count}``. Label-less
    counters (``reservation_reaped_total``) store a single entry with an empty
    label tuple so the render path is uniform.

    The dataclass is mutable but NOT thread-safe at the dict-mutation level.
    We guard ``inc`` and ``render`` with a single Lock so concurrent FastAPI
    workers (or background reapers) cannot race a counter increment against a
    scrape.
    """

    # counters[name][label_tuple] = int
    counters: dict[str, dict[tuple[str, ...], int]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def _ensure(self, name: str) -> dict[tuple[str, ...], int]:
        bucket = self.counters.get(name)
        if bucket is None:
            bucket = {}
            self.counters[name] = bucket
        return bucket

    def inc(self, name: str, /, **labels: str) -> None:
        """Bump ``name{labels}`` by one. Unknown families are silently ignored.

        We choose silent-ignore over raising because hot-path code (the
        router and the guardrail) should not pay the cost of validating
        the metric name on every call. The render path still emits the
        HELP/TYPE lines for known families, so an ``inc("typo_total")``
        becomes invisible — easier to debug than a noisy production log.
        """
        if name not in _METRIC_HELP:
            return
        expected_keys = _METRIC_LABELS.get(name, ())
        # Tolerate call sites that pass extra kwargs (e.g. actor=user-1)
        # by projecting onto the known label set. StrEnum values arrive as
        # plain strings thanks to StrEnum.__str__.
        projected = {key: str(labels[key]) for key in expected_keys if key in labels}
        key = tuple(projected[k] for k in sorted(projected))
        with self._lock:
            bucket = self._ensure(name)
            bucket[key] = bucket.get(key, 0) + 1

    def render(self) -> str:
        """Serialize to Prometheus text exposition format.

        Families are emitted in the order they appear in ``_METRIC_HELP``
        so successive scrapes diff cleanly. Label-less families with no
        recorded value still render a zero sample (``<name> 0``) so scrapers
        always see a numeric value; label-bearing families with no recorded
        value emit only HELP/TYPE lines (the canonical pattern for "metric
        family exists but hasn't been touched yet").
        """
        with self._lock:
            # Snapshot inside the lock so a concurrent inc() cannot race
            # the scrape mid-iteration.
            snapshot = {name: dict(items) for name, items in self.counters.items()}
        lines: list[str] = []
        for family, help_text in _METRIC_HELP.items():
            label_keys = _METRIC_LABELS.get(family, ())
            lines.append(f"# HELP {family} {help_text}")
            lines.append(f"# TYPE {family} counter")
            bucket = snapshot.get(family, {})
            if not bucket:
                if not label_keys:
                    # Label-less family with no samples: render a zero
                    # sample so the scraper always sees a numeric value.
                    lines.append(f"{family} 0")
                # else: family exists but no labelled series yet. Help
                # + Type alone describe it; emitting ``label="" 0`` would
                # confuse scrapers that interpret empty label values.
                continue
            for label_tuple, count in sorted(bucket.items()):
                label_map = dict(zip(label_keys, label_tuple))
                lines.append(f"{family}{_format_labels(label_map)} {count}")
        # Prometheus requires the response to end in a newline.
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Clear all counters. Tests call this between cases for determinism."""
        with self._lock:
            self.counters.clear()


# ---------------------------------------------------------------------------
# Module-level singleton + accessors
# ---------------------------------------------------------------------------

_metrics_singleton: Metrics = Metrics()


def get_metrics() -> Metrics:
    """Return the process-wide Metrics instance."""
    return _metrics_singleton


def reset_metrics() -> None:
    """Zero all counters. Used by tests; never call from production code."""
    _metrics_singleton.reset()


def inc_metrics(name: str, /, **labels: str) -> None:
    """Shortcut for ``get_metrics().inc(name, **labels)``."""
    _metrics_singleton.inc(name, **labels)


def _metric_value(metrics: Metrics, name: str, /, **labels: str) -> int:
    """Return the current counter value for ``name{labels}`` (test helper)."""
    expected_keys = _METRIC_LABELS.get(name, ())
    projected = tuple(labels[k] for k in sorted(expected_keys) if k in labels)
    bucket = metrics.counters.get(name, {})
    return bucket.get(projected, 0)
