"""AfterFlow baseline: deterministic engine vs a raw LLM on the fixed evaluation suite.

Scores the AfterFlow decision engine and a plain DeepSeek chat model against
the same fixed evaluation set, using the same model-agnostic scorer.

Run (from backend/, with DEEPSEEK_API_KEY set):
    PYTHONPATH=.:packages/harness uv run python scripts/evaluate_afterflow_baseline.py

Outputs:
    - a comparison table (engine vs LLM, per-kind and overall)
    - .deer-flow/eval/predictions_{engine,llm}.json for reuse
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.after_sales.decision import decide_resolution
from app.after_sales.evaluation import score_predictions
from app.after_sales.reverse import ReverseInput, VisualEvidence, decide_reverse_fulfillment
from app.after_sales.risk_ops import MetricBucket, detect_after_sales_anomalies
from app.after_sales.schemas import DecisionInput
from deerflow.models.factory import create_chat_model

FIXTURE = Path(__file__).parents[1] / "tests" / "fixtures" / "after_sales_evaluation.json"
OUT_DIR = Path(__file__).parents[1] / ".deer-flow" / "eval"

REFUND_ISSUE_NAMES = {
    "delivery_not_received": "未收到货",
    "damaged_item": "破损",
    "wrong_item": "错发",
    "quality_issue": "质量问题",
    "refund_amount_dispute": "退款金额争议",
}


def load_cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 1) Deterministic engine predictions (mirrors the evaluation-suite test)
# --------------------------------------------------------------------------- #
def engine_prediction(case: dict) -> dict:
    v = case["input"]
    kind = case["kind"]
    if kind == "refund":
        return decide_resolution(
            DecisionInput(
                issue_type=v["issue"],
                order={"order_id": case["id"], "item_paid": v["item"], "shipping_paid": v["shipping"]},
                payment={"refundable_balance": v["balance"]},
                logistics=(
                    {
                        "status": "delivered" if v["pod"] else "in_transit",
                        "proof_of_delivery": v["pod"],
                    }
                    if v["logistics_present"]
                    else None
                ),
                customer_risk={"not_received_claims_180d": v["claims"], "refund_cases_180d": v.get("refund_cases", 0)},
                policy={
                    "policy_id": "EVAL",
                    "version": "1",
                    "covered_issues": [v["issue"]] if v["covered"] else [],
                    "refund_shipping_issues": ["delivery_not_received"],
                    "return_required_issues": ["damaged_item", "wrong_item"],
                    "manual_review_amount": v["manual"],
                    "high_value_amount": v["high"],
                },
                operator_refund_limit=v["operator"],
                visual_evidence_confirmed=v["visual"],
                sign_receipt_hours=v.get("sign_receipt_hours"),
                account_age_days=v.get("account_age_days"),
                historical_refund_rate=v.get("refund_rate"),
                address_changes_30d=v.get("address_changes", 0),
                device_reuse=v.get("device_reuse", False),
            )
        ).model_dump(mode="json")
    if kind == "reverse":
        return decide_reverse_fulfillment(
            ReverseInput(
                issue_type=v["issue"],
                item_value=v["refund"],
                refund_amount=v["refund"],
                return_shipping_cost=v["return_shipping"],
                handling_cost=v["handling"],
                expected_recovery_value=v["recovery"],
                replacement_unit_cost=v.get("replacement_unit_cost", 7_000),
                replacement_shipping_cost=v.get("replacement_shipping_cost", 600),
                replacement_inventory=v["inventory"],
                customer_preference=v["preference"],
                visual_evidence=VisualEvidence(damage_level="major", human_confirmed=v["human"]),
            )
        ).model_dump(mode="json")
    alerts = detect_after_sales_anomalies(
        [
            MetricBucket(
                dimension=v["dimension"],
                value=v["value"],
                issue_type="quality_issue",
                current_orders=v["orders"],
                current_issue_cases=v["issue_cases"],
                previous_orders=v["prev_orders"],
                previous_issue_cases=v["prev_cases"],
                current_loss_amount=v["loss"],
            )
        ]
    )
    return {"alert": bool(alerts), "severity": alerts[0].severity if alerts else None}


# --------------------------------------------------------------------------- #
# 2) Raw-LLM baseline prompts
# --------------------------------------------------------------------------- #
def _refund_prompt(case_id: str, v: dict) -> str:
    logistics = f"有物流证据，承运商签收凭证：{'有' if v['pod'] else '无'}" if v["logistics_present"] else "无物流证据"
    return (
        "你是一名电商售后退款审核员。请仅凭以下案件事实做出判断，只输出一个 JSON 对象，不要任何解释或代码块标记。\n"
        f"案件 ID：{case_id}\n"
        f"客户申报问题类型：{REFUND_ISSUE_NAMES.get(v['issue'], v['issue'])}\n"
        f"商品金额：{v['item']} 分，运费：{v['shipping']} 分\n"
        f"支付可退余额：{v['balance']} 分\n"
        f"该问题是否被售后政策覆盖：{'是' if v['covered'] else '否'}\n"
        f"{logistics}\n"
        f"客户近 180 天'未收到货'索赔次数：{v['claims']}\n"
        f"操作员退款限额：{v['operator']} 分（超过需审批）\n"
        f"政策人工审核金额阈值：{v['manual']} 分；高价值金额阈值：{v['high']} 分\n"
        f"图片证据是否已人工确认：{'是' if v['visual'] else '否'}\n"
        "请输出："
        '{"eligibility": "eligible 或 eligible_with_approval 或 ineligible 或 needs_evidence", '
        '"action": "refund_original_payment 或 return_and_refund 或 manual_review", '
        '"refund_amount": <整数金额，单位分>, '
        '"risk_level": "low 或 medium 或 high", '
        '"approval_required": true 或 false}'
    )


def _reverse_prompt(case_id: str, v: dict) -> str:
    pref = "退货退款" if v["preference"] == "refund" else "换货"
    return (
        "你是一名电商售后逆向履约决策员。请仅凭以下案件事实做出处置方案判断，只输出一个 JSON 对象。\n"
        f"案件 ID：{case_id}\n"
        f"问题类型：{REFUND_ISSUE_NAMES.get(v['issue'], v['issue'])}\n"
        f"商品价值/退款额：{v['refund']} 分\n"
        f"退货运费：{v['return_shipping']} 分，处理成本：{v['handling']} 分\n"
        f"预计残值回收：{v['recovery']} 分\n"
        f"换货库存：{v['inventory']} 件\n"
        f"客户偏好：{pref}\n"
        f"图片证据是否已人工确认：{'是' if v['human'] else '否'}\n"
        "请输出："
        '{"outcome": "decided 或 needs_evidence 或 unsupported", '
        '"action": "refund_without_return 或 return_and_refund 或 replace_after_return 或 manual_review", '
        '"estimated_resolution_cost": <整数，单位分>}'
    )


def _operations_prompt(case_id: str, v: dict) -> str:
    return (
        "你是一名电商售后风控分析师。以下是某对象近 30 天与前一周期 30 天的售后指标，"
        "请判断是否构成需要关注/告警的异常，只输出一个 JSON 对象。\n"
        f"对象：{v['dimension']}={v['value']}（按质量问题统计）\n"
        f"本期：订单 {v['orders']} 单，问题案件 {v['issue_cases']} 件\n"
        f"上期：订单 {v['prev_orders']} 单，问题案件 {v['prev_cases']} 件\n"
        f"本期损失金额：{v['loss']} 分\n"
        "请输出："
        '{"alert": true 或 false, "severity": "medium 或 high 或 null"}'
    )


def build_prompt(case: dict) -> str:
    kind, v = case["kind"], case["input"]
    if kind == "refund":
        return _refund_prompt(case["id"], v)
    if kind == "reverse":
        return _reverse_prompt(case["id"], v)
    return _operations_prompt(case["id"], v)


# --------------------------------------------------------------------------- #
# 3) LLM invocation + JSON repair
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def _coerce(pred: dict, kind: str) -> dict:
    """Coerce obvious types so a stray string doesn't sink the whole case."""
    out: dict = {}
    for k, val in pred.items():
        if k == "refund_amount":
            try:
                out[k] = int(float(str(val).replace(",", "")))
                continue
            except (TypeError, ValueError):
                out[k] = val
        elif k == "estimated_resolution_cost":
            try:
                out[k] = int(float(str(val).replace(",", "")))
                continue
            except (TypeError, ValueError):
                out[k] = val
        elif k == "approval_required" or k == "alert":
            out[k] = str(val).strip().lower() in ("true", "1", "yes", "是")
            continue
        elif k == "severity" and (val is None or str(val).strip().lower() == "null"):
            out[k] = None
            continue
        out[k] = val
    if kind == "refund":
        for field in ("eligibility", "action", "risk_level"):
            if field in out:
                out[field] = str(out[field]).strip().strip('"').lower()
    if kind == "reverse":
        for field in ("outcome", "action"):
            if field in out:
                out[field] = str(out[field]).strip().strip('"').lower()
    return out


def main() -> int:
    model_name = os.environ.get("AF_EVAL_MODEL", "deepseek-v4-flash")
    cases = load_cases()

    print(f"模型: {model_name} | 评测集: {len(cases)} 条", flush=True)

    engine_preds: dict[str, dict] = {}
    for case in cases:
        engine_preds[case["id"]] = engine_prediction(case)

    failures = []
    reuse_llm = os.environ.get("AF_EVAL_REUSE_LLM", "").strip().lower() in {"1", "true", "yes"}
    saved_llm_path = OUT_DIR / "predictions_llm.json"
    if reuse_llm and saved_llm_path.exists():
        print(f"复用已保存的 LLM 预测: {saved_llm_path}", flush=True)
        llm_preds = json.loads(saved_llm_path.read_text(encoding="utf-8"))
        failures = [case["id"] for case in cases if not llm_preds.get(case["id"])]
    else:
        print("调用 LLM 逐条预测…", flush=True)
        model = create_chat_model(model_name)
        llm_preds: dict[str, dict] = {}
        for i, case in enumerate(cases, 1):
            prompt = build_prompt(case)
            try:
                resp = model.invoke(prompt)
                text = getattr(resp, "content", None)
                text = str(text) if text is not None else ""
                pred = _coerce(_extract_json(text), case["kind"])
                if not pred:
                    failures.append(case["id"])
                llm_preds[case["id"]] = pred
            except Exception as exc:  # noqa: BLE001 - report and continue
                print(f"  [warn] {case['id']} 调用失败: {exc}", file=sys.stderr)
                failures.append(case["id"])
                llm_preds[case["id"]] = {}
            if i % 10 == 0:
                print(f"  … {i}/{len(cases)}", flush=True)

    engine_score = score_predictions(cases, engine_preds)
    llm_score = score_predictions(cases, llm_preds)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "predictions_engine.json").write_text(json.dumps(engine_preds, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "predictions_llm.json").write_text(json.dumps(llm_preds, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": model_name,
        "fixture": FIXTURE.name,
        "case_count": len(cases),
        "engine": engine_score,
        "llm": llm_score,
        "parse_failures": failures,
    }
    (OUT_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n================ 对比结果 ================")
    for name, score in (("AfterFlow 决策引擎", engine_score), ("纯 LLM (deepseek-v4-flash)", llm_score)):
        print(f"\n【{name}】")
        print(f"  coverage            : {score['coverage']:.1%}")
        print(f"  field_accuracy      : {score['field_accuracy']:.1%}")
        print(f"  exact_case_accuracy : {score['exact_case_accuracy']:.1%}")
        for kind, st in sorted(score["by_kind"].items()):
            print(f"    - {kind:<10} exact {st['exact']}/{st['total']}")

    if failures:
        print(f"\nLLM 无法解析的 case: {failures}")

    print("\n【纯 LLM 字段准确率】")
    for field, stats in sorted(llm_score["by_field"].items(), key=lambda item: item[1]["accuracy"]):
        print(f"  {field:<28}: {stats['accuracy']:.1%} ({stats['matched']}/{stats['total']})")

    curated = [case for case in cases if case.get("source") == "curated_boundary_v2"]
    if curated:
        curated_score = score_predictions(curated, llm_preds)
        print(f"\n人工边界集 ({len(curated)} 条): field {curated_score['field_accuracy']:.1%}, exact {curated_score['exact_case_accuracy']:.1%}")

    print(f"\n预测与报告已保存: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
