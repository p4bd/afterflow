---
name: after-sales-evidence
description: Use for ecommerce refund evidence collection and contradiction checks across order, payment, logistics, customer history, and policy records.
---

# After-sales Evidence

Build an evidence package before any resolution recommendation.

## Required checks

1. Call `get_after_sales_order` and `get_after_sales_payment` for every case.
2. For `delivery_not_received`, call `get_logistics_evidence`; distinguish carrier proof of delivery from a delivery status label.
3. Call `get_customer_refund_risk` using the customer ID returned by the order tool. A risk signal is not proof of fraud.
4. Call `get_after_sales_policy` using the order region. Preserve the policy ID and version.
5. For damaged or wrong-item cases, require confirmed visual evidence. If unavailable, return `needs_evidence` rather than guessing.

Output facts, contradictions, missing evidence, and source tool names. Do not decide the amount in this skill.
