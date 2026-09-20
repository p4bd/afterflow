---
name: after-sales-resolution
description: Use after ecommerce after-sales evidence has been collected to obtain a deterministic eligibility, refund, risk, action, and approval recommendation.
---

# After-sales Resolution

The deterministic engine owns money and policy decisions; the language model only orchestrates and explains them.

## Procedure

1. Continue the persisted case with `update_after_sales_case` when intake facts change; its deterministic decision is the source of truth.
2. Copy, do not recompute, these result fields: `eligibility`, `action`, `refund_amount`, `risk_level`, `approval_required`, `approval_reasons`, `signals`, `missing_evidence`, and `policy_refs`.
3. If the tool returns an error, report the exact error and stop.
4. If eligibility is `needs_evidence`, request only the listed evidence.
5. If `approval_required` is true, call `create_after_sales_action` from the lead agent and present the result as pending approval.
6. If ineligible, explain the policy reason and offer manual review without fabricating an exception.

Never use arithmetic from conversation text. Only `execute_approved_action` returning `completed` with an external transaction ID proves execution.
