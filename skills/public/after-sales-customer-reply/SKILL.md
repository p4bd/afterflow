---
name: after-sales-customer-reply
description: Use to write a clear ecommerce after-sales customer response from an existing deterministic decision and its approval state.
---

# After-sales Customer Reply

Write a short, operational response grounded in the decision result.

## Response contract

- State the verified issue and what evidence was checked.
- Express money in the customer's currency, converting minor units only for display.
- Distinguish `recommended`, `pending approval`, and `completed`; never blur these states.
- If evidence is missing, list only the next evidence needed and why.
- If approval is required, give no guarantee or invented completion time.
- Include the policy reference in an internal note; expose it to the customer only when useful.
- Avoid accusing the customer based on risk signals. Use neutral language such as “需要进一步核验”.

End with one concrete next step. Do not expose internal fraud scores or tool names.
