---
name: reverse-fulfillment
description: Use after a damaged, wrong-item, or quality claim is eligible to choose return-and-refund, replacement, or refund-without-return from inventory, reverse cost, recovery value, and confirmed visual evidence.
---

# Reverse Fulfillment

1. Require human-confirmed visual evidence from `after-sales-visual-evidence`.
2. Call `get_replacement_inventory` and `get_reverse_fulfillment_costs`; do not invent stock, freight, handling cost, or residual value.
3. Call `evaluate_reverse_fulfillment` with the customer's stated refund/replacement preference.
4. Copy its action, cost, return requirement, and signals without recomputing them.
5. Explain why a low-residual item should not be shipped back, or why recoverable value justifies a return.
6. A recommendation is not a label, replacement, or refund execution. Side effects must become an approved Action Request.
