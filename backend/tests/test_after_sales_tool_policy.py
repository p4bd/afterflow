"""Tool policy / agent loop safety tests (P0-6).

The agent loop's failure modes we are guarding:
1. Loop: agent calls the same read tool 50 times before answering.
2. Drift: agent makes a flood of distinct calls instead of stopping.
3. Waste: agent re-asks for data it has already seen this session.

The `ToolPolicy` enforces:
- max_calls_per_session (default 8): any tool whose session call count
  has reached the cap returns `ToolThrottleExceeded` instead of running.
- max_steps (default 25): the agent middleware short-circuits the loop
  when the step counter crosses this bound.

Both limits are *defensive*: they do NOT change the agent's ability to
solve real problems within normal bounds. They only catch the runaway.
"""

from __future__ import annotations

import pytest

from app.after_sales.tool_policy import (
    StepBudgetExceeded,
    ToolCallThrottle,
    ToolPolicy,
    ToolPolicyStore,
    fingerprint,
)


class TestFingerprint:
    def test_same_args_same_fingerprint(self) -> None:
        assert fingerprint({"a": 1}) == fingerprint({"a": 1})

    def test_key_order_does_not_matter(self) -> None:
        assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


class TestToolPolicy:
    def test_default_policies_are_sane(self) -> None:
        policy = ToolPolicy()
        assert policy.max_steps == 25
        assert policy.max_calls_per_session == 8
        # All AfterFlow tools have at least one entry
        assert "get_after_sales_order" in policy.per_tool_limits

    def test_per_tool_limit_can_be_overridden(self) -> None:
        policy = ToolPolicy(per_tool_overrides={"get_after_sales_order": 2})
        assert policy.per_tool_limits["get_after_sales_order"] == 2

    def test_unknown_tool_uses_default(self) -> None:
        policy = ToolPolicy()
        # Tools not in DEFAULT_PER_TOOL_LIMITS fall back to max_calls_per_session.
        assert policy.cap_for("some_random_tool") == policy.max_calls_per_session


class TestToolPolicyStore:
    @pytest.mark.asyncio
    async def test_first_call_passes(self) -> None:
        store = ToolPolicyStore(policy=ToolPolicy(max_calls_per_session=3))
        verdict = await store.check_and_record(tool_name="get_after_sales_order", args={"order_id": "x"})
        assert isinstance(verdict, ToolCallThrottle)
        assert verdict.allowed is True
        assert verdict.used == 1

    @pytest.mark.asyncio
    async def test_repeated_calls_pass_until_cap(self) -> None:
        store = ToolPolicyStore(policy=ToolPolicy(max_calls_per_session=3))
        for i in range(1, 4):
            verdict = await store.check_and_record(tool_name="get_after_sales_order", args={"order_id": "x"})
            assert verdict.allowed is True
            assert verdict.used == i

    @pytest.mark.asyncio
    async def test_calls_above_cap_are_throttled(self) -> None:
        store = ToolPolicyStore(policy=ToolPolicy(per_tool_overrides={"get_after_sales_order": 2}))
        await store.check_and_record(tool_name="get_after_sales_order", args={"order_id": "x"})
        await store.check_and_record(tool_name="get_after_sales_order", args={"order_id": "y"})
        verdict = await store.check_and_record(tool_name="get_after_sales_order", args={"order_id": "z"})
        assert verdict.allowed is False
        assert verdict.used == 3  # we still record it

    @pytest.mark.asyncio
    async def test_different_tools_have_independent_counters(self) -> None:
        store = ToolPolicyStore(
            policy=ToolPolicy(
                per_tool_overrides={
                    "get_after_sales_order": 1,
                    "get_after_sales_payment": 1,
                },
            )
        )
        v1 = await store.check_and_record(tool_name="get_after_sales_order", args={})
        v2 = await store.check_and_record(tool_name="get_after_sales_payment", args={})
        assert v1.allowed and v2.allowed
        # Second call to either tool must be denied
        v3 = await store.check_and_record(tool_name="get_after_sales_order", args={})
        v4 = await store.check_and_record(tool_name="get_after_sales_payment", args={})
        assert not v3.allowed and not v4.allowed


class TestStepBudget:
    @pytest.mark.asyncio
    async def test_within_budget(self) -> None:
        store = ToolPolicyStore(policy=ToolPolicy(max_steps=3))
        for step in range(3):
            await store.record_step()
        # No exception yet

    @pytest.mark.asyncio
    async def test_exceeding_budget_raises(self) -> None:
        store = ToolPolicyStore(policy=ToolPolicy(max_steps=2))
        await store.record_step()
        await store.record_step()
        with pytest.raises(StepBudgetExceeded) as exc:
            await store.record_step()
        assert exc.value.step == 3
        assert exc.value.budget == 2
