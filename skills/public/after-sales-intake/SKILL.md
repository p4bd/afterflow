---
name: after-sales-intake
description: Use when a customer reports an ecommerce after-sales problem and the agent must identify the order, normalize the issue type, and collect only the minimum facts needed before evidence checks.
---

# After-sales Intake

Turn an unstructured complaint into a traceable case without promising an outcome.

## Procedure

1. Call `create_after_sales_case` with the original complaint immediately. This persists the work even when the order or issue is missing.
2. If the result is `awaiting_clarification`, ask only for its `next_step`; then call `update_after_sales_case` on the same `case_id` with the confirmed answer.
3. Map the complaint to one supported `issue_type`:
   - `delivery_not_received`
   - `damaged_item`
   - `wrong_item`
   - `quality_issue`
   - `refund_amount_dispute`
4. Reuse the verified `evidence_json` and `decision_json` returned by `create_after_sales_case`. Do not repeat order, payment, logistics, risk, policy, inventory, cost, or evaluation calls unless an explicit refresh is requested.
5. Record the customer's requested outcome separately from the policy-supported outcome.
6. Hand off the persisted `case_id`, current status, next step, and compact verified summary. Never ask the user to copy an internal ID.

Never infer an order ID, invent evidence, calculate a refund, or promise approval.
