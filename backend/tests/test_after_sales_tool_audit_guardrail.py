"""Guardrail integration with the tool-call auditor.

The Guardrail decides allow/deny; the auditor records what was decided and
why. Together they answer: "this case was approved because the agent
called these tools and the Guardrail allowed each one."
"""

from __future__ import annotations

import pytest

from app.after_sales.guardrail import AfterSalesGuardrailProvider
from app.after_sales.tool_audit import AuditDecision, InMemoryToolCallAuditor
from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
from deerflow.config.app_config import AppConfig
from deerflow.config.guardrails_config import GuardrailProviderConfig, GuardrailsConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.guardrails.middleware import GuardrailMiddleware
from deerflow.guardrails.provider import GuardrailRequest


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


def test_configured_runtime_constructs_after_sales_guardrail_middleware():
    config = AppConfig(
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
        guardrails=GuardrailsConfig(
            enabled=True,
            fail_closed=True,
            provider=GuardrailProviderConfig(use="app.after_sales.guardrail:AuditedAfterSalesGuardrailProvider"),
        ),
    )

    middlewares = build_lead_runtime_middlewares(app_config=config)

    assert any(isinstance(middleware, GuardrailMiddleware) for middleware in middlewares)


@pytest.mark.asyncio
async def test_guardrail_records_allow_decision():
    InMemoryToolCallAuditor()  # sanity import — wiring is tested below
    provider = AfterSalesGuardrailProvider(repository=None)
    decision = await provider.aevaluate(_req())
    assert decision.allow is True
    # The auditor was not wired into the provider in this test — the wiring
    # is verified in the guardrail-internal audit hook test below.
    assert isinstance(decision.allow, bool)


@pytest.mark.asyncio
async def test_audited_guardrail_records_deny():
    from app.after_sales.guardrail import AuditedAfterSalesGuardrailProvider

    auditor = InMemoryToolCallAuditor()
    provider = AuditedAfterSalesGuardrailProvider(
        repository=None,
        auditor=auditor,
        case_id_resolver=lambda req: req.tool_input.get("case_id"),
    )
    req = _req(is_subagent=True)  # subagent is denied
    decision = await provider.aevaluate(req)
    assert decision.allow is False
    rows = await auditor.list_for_case("CASE-1")
    assert len(rows) == 1
    assert rows[0].decision is AuditDecision.DENY
    assert rows[0].reason_code and "subagent" in rows[0].reason_code


@pytest.mark.asyncio
async def test_audited_guardrail_records_allow():
    from app.after_sales.guardrail import AuditedAfterSalesGuardrailProvider

    auditor = InMemoryToolCallAuditor()
    provider = AuditedAfterSalesGuardrailProvider(
        repository=None,
        auditor=auditor,
        case_id_resolver=lambda req: req.tool_input.get("case_id"),
    )
    decision = await provider.aevaluate(_req())
    assert decision.allow is True
    rows = await auditor.list_for_case("CASE-1")
    assert len(rows) == 1
    assert rows[0].decision is AuditDecision.ALLOW
