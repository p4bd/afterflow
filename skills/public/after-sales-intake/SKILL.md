---
name: after-sales-intake
description: Use when a customer reports an ecommerce after-sales problem and the agent must identify the order, normalize the issue type, and collect only the minimum facts needed before evidence checks.
---

# After-sales Intake

Turn an unstructured complaint into a traceable case without promising an outcome.

## Procedure

1. Obtain exactly one `order_id`. If it is absent or ambiguous, ask for it and stop.
2. Map the complaint to one supported `issue_type`:
   - `delivery_not_received`
   - `damaged_item`
   - `wrong_item`
   - `quality_issue`
   - `refund_amount_dispute`
3. Call `get_after_sales_order`. Treat tool output as the source of truth for paid amounts, customer, region, and order status.
4. Record the customer's requested outcome separately from the policy-supported outcome.
5. Hand off a compact case summary containing `order_id`, `issue_type`, customer statement, requested outcome, and known facts.

Never infer an order ID, invent evidence, calculate a refund, or promise approval.
