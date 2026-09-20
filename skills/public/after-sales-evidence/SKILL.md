---
name: after-sales-evidence
description: Use only to refresh a legacy or incomplete ecommerce after-sales Case. New complaints must use after-sales-intake, whose compound tool already collects this evidence.
---

# After-sales Evidence

Build an evidence package before any resolution recommendation.

## Required checks

1. Reuse the persisted `evidence_json` returned by `create_after_sales_case` or `update_after_sales_case`; those compound tools already collect order, payment, logistics, risk, policy, inventory, and cost evidence. Do not repeat those reads for a fresh Case.
2. Use the granular evidence tools only for an explicit refresh or a legacy Case without a complete evidence snapshot.
3. For `delivery_not_received`, distinguish carrier proof of delivery from a delivery status label.
4. Treat a risk signal as context, not proof of fraud, and preserve policy ID/version from the snapshot.
5. For damaged or wrong-item cases, require confirmed visual evidence. If unavailable, return `needs_evidence` rather than guessing.

Output facts, contradictions, missing evidence, and source tool names. Do not decide the amount in this skill.
