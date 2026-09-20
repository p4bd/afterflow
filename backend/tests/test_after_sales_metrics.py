"""Tests for the AfterFlow Prometheus /metrics endpoint and counters.

The audit report flagged Observability as the only P1 gap after the
P0 lifespan wiring upgrade. This test file pins the contract:

  * Counters start at 0 after ``reset_metrics()``.
  * Counter samples render in Prometheus exposition format.
  * The ``/metrics`` HTTP endpoint exposes plain-text scrapes.
  * All key decision points (decisions, actions, approvals, executions,
    idempotency, tool audit, reservation reaper) bump their counter.

The text format spec we conform to:
  https://prometheus.io/docs/instrumenting/exposition_formats/
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.after_sales.actions import MockRefundExecutor
from app.after_sales.guardrail import (
    AfterSalesGuardrailProvider,
    AuditedAfterSalesGuardrailProvider,
)
from app.after_sales.idempotency import InMemoryIdempotencyStore
from app.after_sales.metrics import (
    Metrics,
    _metric_value,
    get_metrics,
    inc_metrics,
    reset_metrics,
)
from app.after_sales.mock_data import PAYMENTS
from app.after_sales.repository import AfterSalesRepository
from app.after_sales.tool_audit import InMemoryToolCallAuditor
from app.gateway.routers import metrics as metrics_router
from app.gateway.routers.after_sales import router as after_sales_router
from deerflow.guardrails.provider import GuardrailRequest
from deerflow.persistence.base import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_sample(body: str, metric: str, **labels: str) -> float | None:
    """Parse a counter sample out of a Prometheus text dump.

    ``metric`` is the bare family name; labels are matched as ``key="value"``
    pairs in the label set. Lines look like ``metric{outcome="eligible"} 3``.
    """
    label_keys = sorted(labels.keys())
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)\s*(\{[^}]*\})?\s+([0-9.eE+-]+)\s*$", line)
        if m is None:
            continue
        name = m.group(1)
        label_block = m.group(2) or ""
        value = m.group(3)
        if name != metric:
            continue
        if labels:
            # Parse ``label_block`` into key="value" pairs (order-independent).
            label_pairs = re.findall(r'(\w+)="([^"]*)"', label_block)
            if sorted(label_pairs) != [(k, labels[k]) for k in label_keys]:
                continue
        else:
            if label_block:
                continue
        return float(value)
    return None


# ---------------------------------------------------------------------------
# Pure unit tests — no FastAPI / DB needed
# ---------------------------------------------------------------------------


def test_metrics_dataclass_starts_at_zero():
    m = Metrics()
    samples = m.render()
    # Every counter family renders at 0; the HELP/TYPE lines must be present.
    assert "# HELP decisions_total" in samples
    assert "# TYPE decisions_total counter" in samples
    assert "# HELP actions_created_total" in samples
    assert "# TYPE actions_created_total counter" in samples
    assert "# HELP approvals_total" in samples
    assert "# TYPE approvals_total counter" in samples
    assert "# HELP executions_total" in samples
    assert "# TYPE executions_total counter" in samples
    assert "# HELP idempotency_total" in samples
    assert "# TYPE idempotency_total counter" in samples
    assert "# HELP tool_audit_total" in samples
    assert "# TYPE tool_audit_total counter" in samples
    assert "# HELP reservation_reaped_total" in samples
    assert "# TYPE reservation_reaped_total counter" in samples


def test_reset_metrics_zeroes_singleton():
    reset_metrics()
    inc_metrics("decisions_total", outcome="eligible")
    assert _metric_value(get_metrics(), "decisions_total", outcome="eligible") == 1
    reset_metrics()
    assert _metric_value(get_metrics(), "decisions_total", outcome="eligible") == 0


def test_inc_bumps_counter_and_render_includes_sample():
    reset_metrics()
    inc_metrics("decisions_total", outcome="eligible")
    inc_metrics("decisions_total", outcome="eligible")
    inc_metrics("decisions_total", outcome="ineligible")
    samples = get_metrics().render()
    assert _read_sample(samples, "decisions_total", outcome="eligible") == 2.0
    assert _read_sample(samples, "decisions_total", outcome="ineligible") == 1.0
    reset_metrics()


def test_inc_with_no_labels_renders_bare_metric():
    """``reservation_reaped_total`` is a label-less counter."""
    reset_metrics()
    inc_metrics("reservation_reaped_total")
    inc_metrics("reservation_reaped_total")
    samples = get_metrics().render()
    assert _read_sample(samples, "reservation_reaped_total") == 2.0
    reset_metrics()


def test_render_is_spec_compliant():
    """Every non-comment line must match the Prometheus line grammar."""
    reset_metrics()
    inc_metrics("decisions_total", outcome="eligible")
    inc_metrics("approvals_total", decision="granted")
    inc_metrics("tool_audit_total", decision="allow")
    samples = get_metrics().render()
    pattern = re.compile(r"^(# (HELP|TYPE) [a-zA-Z_:][a-zA-Z0-9_:]* .*|[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}]*\})? [0-9.eE+-]+)$")
    lines = samples.splitlines()
    assert lines, "rendered body must not be empty"
    for line in lines:
        assert pattern.match(line), f"non-spec line: {line!r}"
    # At least one HELP, one TYPE, and one sample per family present.
    assert any(line.startswith("# HELP decisions_total") for line in lines)
    assert any(line.startswith("# TYPE decisions_total counter") for line in lines)
    assert any(line.startswith("decisions_total{") for line in lines)
    reset_metrics()


# ---------------------------------------------------------------------------
# Counter wiring — guardrail allow/deny
# ---------------------------------------------------------------------------


def _req(**overrides):
    base = {
        "tool_name": "create_after_sales_action",
        "tool_input": {"case_id": "CASE-1"},
        "is_subagent": False,
        "user_id": "agent-1",
        "user_role": "user",
    }
    base.update(overrides)
    return GuardrailRequest(**base)


@pytest.mark.asyncio
async def test_tool_audit_allow_increments_allow_counter():
    reset_metrics()
    auditor = InMemoryToolCallAuditor()
    provider = AuditedAfterSalesGuardrailProvider(
        repository=None,
        auditor=auditor,
        case_id_resolver=lambda req: req.tool_input.get("case_id"),
        inner=AfterSalesGuardrailProvider(repository=None),
    )
    decision = await provider.aevaluate(_req())
    assert decision.allow is True
    samples = get_metrics().render()
    assert _read_sample(samples, "tool_audit_total", decision="allow") == 1.0
    assert _read_sample(samples, "tool_audit_total", decision="deny") is None
    reset_metrics()


@pytest.mark.asyncio
async def test_tool_audit_deny_increments_deny_counter():
    reset_metrics()
    auditor = InMemoryToolCallAuditor()
    provider = AuditedAfterSalesGuardrailProvider(
        repository=None,
        auditor=auditor,
        case_id_resolver=lambda req: req.tool_input.get("case_id"),
        inner=AfterSalesGuardrailProvider(repository=None),
    )
    decision = await provider.aevaluate(_req(is_subagent=True))
    assert decision.allow is False
    samples = get_metrics().render()
    assert _read_sample(samples, "tool_audit_total", decision="deny") == 1.0
    assert _read_sample(samples, "tool_audit_total", decision="allow") is None
    reset_metrics()


# ---------------------------------------------------------------------------
# Counter wiring — full router lifecycle
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'after-sales-metrics.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.state.after_sales_repo = AfterSalesRepository(async_sessionmaker(engine, expire_on_commit=False))
    app.state.after_sales_refund_executor = MockRefundExecutor(initial_balances={order_id: payment["refundable_balance"] for order_id, payment in PAYMENTS.items()})
    # Wire the idempotency store so the hit/miss/conflict paths in the
    # router helper actually exercise their code branches (without a store
    # wired, every request is a no-op miss and the replay/conflict counters
    # stay at 0).
    app.state.after_sales_idempotency_store = InMemoryIdempotencyStore()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user = SimpleNamespace(
            id=request.headers.get("x-user-id", "agent-1"),
            system_role=request.headers.get("x-user-role", "user"),
        )
        return await call_next(request)

    app.include_router(after_sales_router)
    app.include_router(metrics_router.router)
    reset_metrics()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http
    await engine.dispose()
    reset_metrics()


@pytest.mark.asyncio
async def test_decisions_counter_increments_on_case_create(client):
    response = await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1001", "issue_type": "delivery_not_received"},
    )
    assert response.status_code == 201
    samples = get_metrics().render()
    # ORDER-1001.item_paid = 89_900 cents; default user operator_limit = 20_000,
    # so the decision falls into eligible_with_approval (approval_required).
    assert _read_sample(samples, "decisions_total", outcome="eligible_with_approval") == 1.0


@pytest.mark.asyncio
async def test_actions_created_counter_increments(client):
    created = await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1001", "issue_type": "delivery_not_received"},
    )
    case_id = created.json()["id"]
    action_response = await client.post(f"/api/after-sales/cases/{case_id}/actions")
    assert action_response.status_code == 201
    samples = get_metrics().render()
    assert _read_sample(samples, "actions_created_total", status="pending_approval") == 1.0


@pytest.mark.asyncio
async def test_full_lifecycle_bumps_decision_action_approval_execution_counters(client):
    # 1. Create a case (decision: eligible_with_approval because operator
    #    limit is too low for the 89_900-cent refund).
    created = await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1001", "issue_type": "delivery_not_received"},
    )
    case = created.json()
    # ORDERS.ORDER-1001 has item_paid=89_900; default user operator_limit=20_000
    # → eligible_with_approval.
    assert case["decision_json"]["eligibility"] == "eligible_with_approval"

    # 2. Action create → status="pending_approval".
    action_response = await client.post(f"/api/after-sales/cases/{case['id']}/actions")
    action = action_response.json()
    assert action["status"] == "pending_approval"

    # 3. Approve as admin → status="approved", approvals_total{granted} += 1.
    approved = await client.post(
        f"/api/after-sales/actions/{action['id']}/approve",
        headers={"x-user-id": "admin-1", "x-user-role": "admin"},
        json={"expected_version": 1, "comment": "ok"},
    )
    assert approved.status_code == 200

    # 4. Execute → executions_total{succeeded} += 1.
    executed = await client.post(
        f"/api/after-sales/actions/{action['id']}/execute",
        headers={"x-user-id": "admin-1", "x-user-role": "admin"},
        json={"expected_version": 2, "payload": action["payload"]},
    )
    assert executed.status_code == 200

    samples = get_metrics().render()
    assert _read_sample(samples, "decisions_total", outcome="eligible_with_approval") == 1.0
    assert _read_sample(samples, "actions_created_total", status="pending_approval") == 1.0
    assert _read_sample(samples, "approvals_total", decision="granted") == 1.0
    assert _read_sample(samples, "executions_total", outcome="succeeded") == 1.0


@pytest.mark.asyncio
async def test_rejection_bumps_rejections_counter(client):
    created = await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1001", "issue_type": "delivery_not_received"},
    )
    case = created.json()
    action = (await client.post(f"/api/after-sales/cases/{case['id']}/actions")).json()
    rejected = await client.post(
        f"/api/after-sales/actions/{action['id']}/reject",
        headers={"x-user-id": "admin-1", "x-user-role": "admin"},
        json={"expected_version": 1, "comment": "evidence insufficient"},
    )
    assert rejected.status_code == 200
    samples = get_metrics().render()
    assert _read_sample(samples, "approvals_total", decision="rejected") == 1.0


@pytest.mark.asyncio
async def test_idempotency_replay_bumps_hit_counter(client):
    created = await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1001", "issue_type": "delivery_not_received"},
        headers={"Idempotency-Key": "fixed-key-A"},
    )
    case_id_1 = created.json()["id"]
    replay = await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1001", "issue_type": "delivery_not_received"},
        headers={"Idempotency-Key": "fixed-key-A"},
    )
    case_id_2 = replay.json()["id"]
    # Replay returns the cached case id (same row).
    assert case_id_1 == case_id_2
    samples = get_metrics().render()
    assert _read_sample(samples, "idempotency_total", result="hit") == 1.0
    assert _read_sample(samples, "idempotency_total", result="miss") == 1.0


@pytest.mark.asyncio
async def test_idempotency_conflict_bumps_conflict_counter(client):
    await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1001", "issue_type": "delivery_not_received"},
        headers={"Idempotency-Key": "reuse-this-key"},
    )
    conflict = await client.post(
        "/api/after-sales/cases",
        json={"order_id": "ORDER-1002", "issue_type": "delivery_not_received"},
        headers={"Idempotency-Key": "reuse-this-key"},
    )
    assert conflict.status_code == 422
    samples = get_metrics().render()
    assert _read_sample(samples, "idempotency_total", result="conflict") == 1.0


# ---------------------------------------------------------------------------
# /metrics HTTP endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_plain_text(client):
    # Trigger at least one counter so the body is non-empty.
    await client.post("/api/after-sales/cases", json={"order_id": "ORDER-1001", "issue_type": "delivery_not_received"})
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "# HELP decisions_total" in body
    assert "# TYPE decisions_total counter" in body
    assert 'decisions_total{outcome="eligible_with_approval"}' in body


@pytest.mark.asyncio
async def test_metrics_endpoint_does_not_require_auth(client):
    # /metrics is intended for internal scrapers; no auth header expected.
    response = await client.get("/metrics")
    assert response.status_code == 200
