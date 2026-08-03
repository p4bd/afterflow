---
name: after-sales-risk-operations
description: Use for scheduled ecommerce after-sales operations review to detect abnormal SKU damage, carrier non-delivery, warehouse wrong-item, or refund-loss patterns using denominator-aware metrics.
---

# After-sales Risk Operations

1. Call `scan_after_sales_operations`; never rank raw complaint counts without order-volume denominators.
2. Report current rate, previous rate, issue type, loss amount, sample floor, and the exact alert signals.
3. Separate an operational anomaly from customer-level fraud risk.
4. For each alert, propose one bounded investigation: inspect a SKU batch, carrier route, or warehouse pick process.
5. Do not claim root cause from correlation. State what additional evidence would confirm it.
6. Produce a short alert table ordered by severity and estimated loss, then a named owner and next review time.
