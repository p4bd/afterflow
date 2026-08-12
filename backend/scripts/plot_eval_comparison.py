"""Render the AfterFlow vs raw-LLM evaluation comparison as a self-contained SVG.

Dependency-free. Reads the saved baseline predictions and the gold set, then
writes docs/images/eval-comparison.svg (renders in GitHub and browsers).

Run (from backend/):
    PYTHONPATH=.:packages/harness uv run python scripts/plot_eval_comparison.py
"""

from __future__ import annotations

import json
from pathlib import Path

from app.after_sales.evaluation import score_predictions

GOLD = Path("tests/fixtures/after_sales_evaluation.json")
PREDS_DIR = Path(".deer-flow/eval")
OUT = Path("../docs/images/eval-comparison.svg")

ENGINE_COLOR = "#2563eb"
LLM_COLOR = "#94a3b8"

KIND_LABELS = [("overall", "整体"), ("refund", "退款"), ("reverse", "逆向履约"), ("operations", "运营预警")]


def _exact_pct(score: dict, kind: str) -> float:
    if kind == "overall":
        return score["exact_case_accuracy"] * 100.0
    st = score["by_kind"][kind]
    return st["exact"] / st["total"] * 100.0


def main() -> None:
    cases = json.loads(GOLD.read_text(encoding="utf-8"))
    engine = json.loads((PREDS_DIR / "predictions_engine.json").read_text(encoding="utf-8"))
    llm = json.loads((PREDS_DIR / "predictions_llm.json").read_text(encoding="utf-8"))
    es = score_predictions(cases, engine)
    ls = score_predictions(cases, llm)

    data = [(_exact_pct(es, key), _exact_pct(ls, key)) for key, _ in KIND_LABELS]

    W, H = 860, 460
    margin_l, margin_r, margin_t, margin_b = 90, 30, 60, 70
    plot_w = W - margin_l - margin_r
    plot_h = H - margin_t - margin_b
    baseline_y = margin_t + plot_h

    group_w = plot_w / len(data)
    bar_w, gap = 58, 14

    parts: list[str] = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">')
    # background
    parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')
    # title
    parts.append(f'<text x="{margin_l}" y="34" font-size="20" font-weight="700" fill="#0f172a">AfterFlow 决策引擎 vs 纯 LLM — 整案准确率（{len(cases)} 条评测集）</text>')
    # gridlines + y labels
    for tick in (0, 20, 40, 60, 80, 100):
        y = baseline_y - (tick / 100) * plot_h
        parts.append(f'<line x1="{margin_l}" y1="{y:.1f}" x2="{W - margin_r}" y2="{y:.1f}" stroke="#e2e8f0" stroke-width="1"/>')
        parts.append(f'<text x="{margin_l - 10}" y="{y + 4:.1f}" font-size="12" fill="#64748b" text-anchor="end">{tick}%</text>')

    # bars
    for i, (eng, llm) in enumerate(data):
        cx = margin_l + group_w * i + group_w / 2
        x_eng = cx - gap / 2 - bar_w
        x_llm = cx + gap / 2

        h_eng = (eng / 100) * plot_h
        h_llm = (llm / 100) * plot_h

        parts.append(f'<rect x="{x_eng:.1f}" y="{baseline_y - h_eng:.1f}" width="{bar_w}" height="{max(h_eng, 0):.1f}" rx="4" fill="{ENGINE_COLOR}"/>')
        parts.append(f'<rect x="{x_llm:.1f}" y="{baseline_y - h_llm:.1f}" width="{bar_w}" height="{max(h_llm, 0):.1f}" rx="4" fill="{LLM_COLOR}"/>')
        # value labels
        parts.append(f'<text x="{x_eng + bar_w / 2:.1f}" y="{baseline_y - h_eng - 6:.1f}" font-size="13" font-weight="600" fill="{ENGINE_COLOR}" text-anchor="middle">{eng:.0f}%</text>')
        parts.append(f'<text x="{x_llm + bar_w / 2:.1f}" y="{baseline_y - h_llm - 6:.1f}" font-size="13" font-weight="600" fill="{LLM_COLOR}" text-anchor="middle">{llm:.0f}%</text>')
        # group label
        parts.append(f'<text x="{cx:.1f}" y="{baseline_y + 24}" font-size="14" fill="#0f172a" text-anchor="middle">{KIND_LABELS[i][1]}</text>')

    # legend
    lx = margin_l
    parts.append(f'<rect x="{lx}" y="{baseline_y + 48}" width="14" height="14" rx="3" fill="{ENGINE_COLOR}"/>')
    parts.append(f'<text x="{lx + 20}" y="{baseline_y + 61}" font-size="13" fill="#334155">AfterFlow 决策引擎</text>')
    parts.append(f'<rect x="{lx + 190}" y="{baseline_y + 48}" width="14" height="14" rx="3" fill="{LLM_COLOR}"/>')
    parts.append(f'<text x="{lx + 210}" y="{baseline_y + 61}" font-size="13" fill="#334155">纯 LLM (deepseek-v4-flash)</text>')

    parts.append("</svg>")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"SVG 已生成: {OUT.resolve()}")


if __name__ == "__main__":
    main()
