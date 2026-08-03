---
name: after-sales-visual-evidence
description: Use when an ecommerce damaged, wrong-item, or quality claim includes photos and the agent must extract structured visual evidence without treating model vision as final proof.
---

# After-sales Visual Evidence

1. Use `view_image` on every supplied image; do not infer from filenames.
2. Record visible facts separately: product identity/serial, packaging, affected area, damage level (`none`, `minor`, `major`, `destroyed`), and image-quality limitations.
3. Never infer cause, intent, authenticity, or fraud from pixels alone.
4. Ask a human to confirm product identity and damage level before setting `human_confirmed=true` for `evaluate_reverse_fulfillment`.
5. If serial text is unreadable or images conflict, return the exact missing view instead of choosing the most convenient interpretation.

Output a structured evidence summary plus a neutral human-confirmation question. Preserve corrections as the authoritative value.
