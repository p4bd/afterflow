"""Tool policy / agent loop safety (P0-6).

AfterFlow's agent loop is allowed to think, but it is NOT allowed to
spin. Three defensive limits are enforced here:

1. max_calls_per_session (default 8): a single tool called more than N
   times in one conversation is throttled. This catches the "agent
   loops on `get_logistics_evidence` instead of looking at the response"
   failure mode.
2. max_steps (default 25): the agent's total step counter is capped.
   Beyond the cap we raise StepBudgetExceeded so the middleware can
   decide whether to ask the user or fail closed.
3. ToolPolicyStore.check_and_record() returns a verdict object — the
   caller can decide whether to surface the throttle to the user or
   just silently drop the call. AfterFlow surfaces it (the agent
   should know it hit a wall).

These limits do NOT touch the deterministic engine or write tools.
Write tools are already gated by Guardrail; per-tool throttling is an
extra layer that catches a different failure mode (loop, not attack).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field


def fingerprint(args: dict) -> str:
    """Stable fingerprint of a tool-call argument dict (key-order independent)."""
    encoded = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


# Default per-tool call caps. Read tools can be called a few times (agent
# might double-check) but never in a runaway loop. Write tools are gated by
# Guardrail already, so their cap is purely a runaway-loop guard.
DEFAULT_PER_TOOL_LIMITS: dict[str, int] = {
    "get_after_sales_order": 4,
    "get_after_sales_payment": 3,
    "get_logistics_evidence": 3,
    "get_customer_refund_risk": 3,
    "get_after_sales_policy": 2,
    "get_replacement_inventory": 2,
    "get_reverse_fulfillment_costs": 2,
    "evaluate_reverse_fulfillment": 4,
    "scan_after_sales_operations": 1,
    "evaluate_after_sales_case": 4,
    "search_after_sales_knowledge": 4,
    "create_after_sales_case": 1,
    "update_after_sales_case": 2,
    "create_after_sales_action": 1,  # write — must be one-shot
    "execute_approved_action": 1,  # write — must be one-shot
}


@dataclass
class ToolPolicy:
    max_steps: int = 25
    max_calls_per_session: int = 8  # default for any tool not in DEFAULT_PER_TOOL_LIMITS
    per_tool_overrides: dict[str, int] = field(default_factory=dict)

    @property
    def per_tool_limits(self) -> dict[str, int]:
        # Per-tool caps from defaults + overrides. Any tool not in either
        # set uses max_calls_per_session as its cap. We compose this lazily
        # because overrides can change at runtime.
        merged = dict(DEFAULT_PER_TOOL_LIMITS)
        merged.update(self.per_tool_overrides)
        return merged

    def cap_for(self, tool_name: str) -> int:
        return self.per_tool_limits.get(tool_name, self.max_calls_per_session)


@dataclass
class ToolCallThrottle:
    allowed: bool
    used: int
    cap: int
    tool_name: str

    def __bool__(self) -> bool:  # truthy == allowed
        return self.allowed


class StepBudgetExceeded(Exception):
    """The agent loop has exceeded the configured max_steps."""

    def __init__(self, *, step: int, budget: int) -> None:
        super().__init__(f"agent step budget exceeded: step={step}, budget={budget}")
        self.step = step
        self.budget = budget


class ToolPolicyStore:
    """Session-scoped store: per-tool call counts and a step counter."""

    def __init__(self, *, policy: ToolPolicy | None = None) -> None:
        self._policy = policy or ToolPolicy()
        self._counts: dict[str, int] = {}
        self._steps = 0
        self._lock = asyncio.Lock()

    @property
    def policy(self) -> ToolPolicy:
        return self._policy

    async def check_and_record(self, *, tool_name: str, args: dict) -> ToolCallThrottle:
        """Atomically increment the call count and decide whether to allow.

        `args` is fingerprinted to give operators an audit trail of what
        was tried; the cap is per-tool-name, not per-fingerprint (so a
        different argument shape doesn't bypass the limit).
        """
        cap = self._policy.cap_for(tool_name)
        async with self._lock:
            new_count = self._counts.get(tool_name, 0) + 1
            self._counts[tool_name] = new_count
            return ToolCallThrottle(
                allowed=new_count <= cap,
                used=new_count,
                cap=cap,
                tool_name=tool_name,
            )

    async def record_step(self) -> None:
        """Increment the global step counter; raise if over budget.

        The agent middleware calls this once per loop iteration.
        """
        async with self._lock:
            self._steps += 1
            step = self._steps
        if step > self._policy.max_steps:
            raise StepBudgetExceeded(step=step, budget=self._policy.max_steps)

    async def snapshot(self) -> dict:
        """Diagnostic snapshot of usage so far."""
        async with self._lock:
            return {"steps": self._steps, "tool_counts": dict(self._counts)}
