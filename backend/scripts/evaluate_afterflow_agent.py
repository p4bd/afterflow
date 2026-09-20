"""Run or offline-rescore natural-language AfterFlow Agent tasks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver

from app.after_sales.repository import AfterSalesRepository
from deerflow.client import DeerFlowClient
from deerflow.config.app_config import get_app_config
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
from deerflow.runtime.user_context import reset_current_user, set_current_user

ROOT = Path(__file__).parents[1]
TASKS = ROOT / "tests" / "fixtures" / "after_sales_agent_tasks.json"
OUT = ROOT / ".deer-flow" / "eval" / "agent"
WORKFLOW_INPUTS = (
    ROOT.parent / "skills" / "public" / "after-sales-intake" / "SKILL.md",
    ROOT.parent / "skills" / "public" / "after-sales-evidence" / "SKILL.md",
    ROOT.parent / "skills" / "public" / "after-sales-resolution" / "SKILL.md",
    ROOT.parent / "skills" / "public" / "reverse-fulfillment" / "SKILL.md",
)
AFTER_SALES_SKILLS = {
    "after-sales-customer-reply",
    "after-sales-evidence",
    "after-sales-intake",
    "after-sales-resolution",
    "after-sales-visual-evidence",
    "reverse-fulfillment",
}
SCORE_FIELDS = (
    "task_completed",
    "case_created",
    "order_id_match",
    "issue_type_match",
    "fields_joint_match",
    "stage_match",
    "human_intervention_match",
    "required_human_intervention_present",
    "unwanted_human_intervention_absent",
    "forbidden_tools_absent",
    "forbidden_side_effects_absent",
    "agent_reply_consistent",
    "draft_reply_consistent",
)
COMPOUND_INTAKE_CONTEXT_TOOLS = frozenset(
    {
        "evaluate_after_sales_case",
        "evaluate_reverse_fulfillment",
        "get_after_sales_order",
        "get_after_sales_payment",
        "get_after_sales_policy",
        "get_customer_refund_risk",
        "get_logistics_evidence",
        "get_replacement_inventory",
        "get_reverse_fulfillment_costs",
    }
)


def _async(coro):
    return asyncio.run(coro)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workflow_input_hashes() -> dict[str, str]:
    return {str(path.relative_to(ROOT.parent)): _sha256(path) for path in WORKFLOW_INPUTS}


def _route_agent_input(text: str, routing: str) -> str:
    return f"/after-sales-intake\n{text}" if routing == "after-sales" else text


def _tool_result_error(content: str) -> str | None:
    try:
        value = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        value = None
    if isinstance(value, dict) and value.get("error"):
        return str(value["error"])
    text = str(content).strip()
    return text[:500] if text.lower().startswith(("error:", "tool error")) else None


def _has_unnegated_claim(text: str, claims: tuple[str, ...]) -> bool:
    for claim in claims:
        start = 0
        while (index := text.find(claim, start)) >= 0:
            if not any(marker in text[max(0, index - 24) : index] for marker in ("无法", "不能", "未", "没有", "尚未", "不代表")):
                return True
            start = index + len(claim)
    return False


def _reply_consistent(reply: str, case: dict | None, actions: list[dict] | None) -> bool | None:
    if not reply.strip():
        return False
    if case is None:
        return not any(claim in reply for claim in ("案件已", "已保存案件", "当前待审批", "已经提交审批"))
    if case["status"] != "completed" and _has_unnegated_claim(reply, ("已到账", "退款成功", "补发已出库")):
        return False
    action_claims = ("主管已批准", "已生成退款申请", "已经提交审批", "退款申请已生成")
    if actions is None and any(claim in reply for claim in action_claims):
        return None
    if actions is not None:
        if not any(action.get("status") == "approved" for action in actions) and "主管已批准" in reply:
            return False
        if not actions and any(claim in reply for claim in action_claims[1:]):
            return False
    return True


def score_record(record: dict, expect: dict) -> dict:
    case = record.get("terminal_case")
    actions = record.get("terminal_actions") if "terminal_actions" in record else None
    tool_calls = record.get("tool_calls") or [{"id": f"legacy-{index}", "name": name} for index, name in enumerate(record.get("tool_names") or [])]
    forbidden = set(expect.get("forbidden_tools") or [])
    forbidden_actions = set(expect.get("forbidden_action_types") or [])
    if case is None:
        order_match = issue_match = stage_match = human_match = False
        draft_consistent = False
    else:
        order_match = case.get("order_id") == expect.get("order_id")
        issue_match = case.get("issue_type") == expect.get("issue_type")
        stage_match = case.get("status") == expect.get("status")
        actual_human = case.get("status") in {"awaiting_clarification", "awaiting_evidence", "pending_approval"}
        human_match = actual_human == expect.get("human_intervention")
        draft_consistent = _reply_consistent(str(case.get("reply_draft") or ""), case, actions)
    action_types = {action.get("action_type") for action in actions or []}
    expects_human = expect.get("human_intervention")
    forbidden_tools_absent = not any(call.get("name") in forbidden for call in tool_calls) if forbidden else None
    forbidden_side_effects_absent = not bool(action_types & forbidden_actions) if forbidden_actions and actions is not None else None
    required_checks = [forbidden_tools_absent] if forbidden else []
    if forbidden_actions:
        required_checks.append(forbidden_side_effects_absent)
    base_completed = record.get("error") is None and case is not None and order_match and issue_match and stage_match and human_match
    task_completed = False if not base_completed or False in required_checks else None if None in required_checks else True
    return {
        "task_completed": task_completed,
        "case_created": case is not None,
        "order_id_match": order_match,
        "issue_type_match": issue_match,
        "fields_joint_match": order_match and issue_match,
        "stage_match": stage_match,
        "human_intervention_match": human_match,
        "required_human_intervention_present": human_match if expects_human is True else None,
        "unwanted_human_intervention_absent": human_match if expects_human is False else None,
        "forbidden_tools_absent": forbidden_tools_absent,
        "forbidden_side_effects_absent": forbidden_side_effects_absent,
        "agent_reply_consistent": _reply_consistent(str(record.get("output") or ""), case, actions),
        "draft_reply_consistent": draft_consistent,
    }


def _matches(case: dict | None, expect: dict) -> dict:
    """Compatibility for callers of the previous private helper."""
    return score_record({"terminal_case": case, "output": (case or {}).get("reply_draft", "")}, expect)


def _summary(records: list[dict], *, metadata: dict, raw_name: str) -> dict:
    summary = {**metadata, "run_count": len(records), "raw_records": raw_name}
    summary["successful_agent_runs"] = sum(record.get("error") is None for record in records)
    for field in SCORE_FIELDS:
        observed = [record["score"][field] for record in records if record["score"].get(field) is not None]
        summary[f"{field}_observed"] = len(observed)
        summary[f"{field}_accuracy"] = sum(observed) / len(observed) if observed else None
    tool_counts = [record["tool_count"] for record in records if isinstance(record.get("tool_count"), (int, float))]
    tool_names = [name for record in records for name in (record.get("tool_names") or [call.get("name") for call in record.get("tool_calls", [])]) if name]
    summary["total_tool_calls"] = sum(tool_counts)
    summary["average_tool_count"] = statistics.mean(tool_counts) if tool_counts else None
    summary["tool_call_counts"] = dict(sorted(Counter(tool_names).items()))
    redundant_by_run = []
    redundant_with_compound_by_run = []
    for record in records:
        names = record.get("tool_names") or [call.get("name") for call in record.get("tool_calls", [])]
        intake_index = names.index("create_after_sales_case") if "create_after_sales_case" in names else len(names)
        redundant_by_run.append(sum(name in COMPOUND_INTAKE_CONTEXT_TOOLS for name in names[intake_index + 1 :]))
        redundant_with_compound_by_run.append(sum(name in COMPOUND_INTAKE_CONTEXT_TOOLS for name in names) if intake_index < len(names) else 0)
    summary["redundant_context_calls_after_intake"] = sum(redundant_by_run)
    summary["runs_with_redundant_context_calls_after_intake"] = sum(count > 0 for count in redundant_by_run)
    summary["redundant_context_calls_with_compound_intake"] = sum(redundant_with_compound_by_run)
    summary["runs_with_redundant_context_calls_with_compound_intake"] = sum(count > 0 for count in redundant_with_compound_by_run)
    tool_results = [call for record in records for call in record.get("tool_calls", []) if call.get("completed") is True]
    summary["tool_results_observed"] = len(tool_results)
    summary["tool_success_rate"] = sum(call.get("error") is None for call in tool_results) / len(tool_results) if tool_results else None
    tool_latencies = [call["latency_ms"] for call in tool_results if isinstance(call.get("latency_ms"), (int, float))]
    summary["average_tool_latency_ms"] = statistics.mean(tool_latencies) if tool_latencies else None
    summary["tool_latency_p50_ms"] = statistics.median(tool_latencies) if tool_latencies else None
    summary["tool_latency_p95_ms"] = sorted(tool_latencies)[min(len(tool_latencies) - 1, int(len(tool_latencies) * 0.95))] if tool_latencies else None
    latencies = [record["latency_ms"] for record in records if isinstance(record.get("latency_ms"), (int, float))]
    summary["average_latency_ms"] = statistics.mean(latencies) if latencies else None
    summary["latency_p50_ms"] = statistics.median(latencies) if latencies else None
    summary["latency_p95_ms"] = sorted(latencies)[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
    observed_tokens = [record["token_usage"] for record in records if record.get("token_usage") is not None]
    summary["token_usage_available_runs"] = len(observed_tokens)
    summary["token_usage_coverage"] = len(observed_tokens) / len(records) if records else None
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        summary[f"average_{field}"] = statistics.mean(usage[field] for usage in observed_tokens) if observed_tokens else None
    summary["task_completion_by_split"] = {}
    for split in sorted({record.get("split", "historical") for record in records}):
        observed = [record["score"]["task_completed"] for record in records if record.get("split", "historical") == split and record["score"].get("task_completed") is not None]
        summary["task_completion_by_split"][split] = {
            "completed": sum(observed),
            "observed": len(observed),
            "accuracy": sum(observed) / len(observed) if observed else None,
        }
    summary["failures"] = [
        {"task_id": record["task_id"], "attempt": record["attempt"], "error": record.get("error"), "score": record["score"]} for record in records if record.get("error") or any(value is False for value in record["score"].values())
    ]
    return summary


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _offline(source: Path, tasks: list[dict], task_path: Path, output_dir: Path) -> int:
    task_by_id = {task["id"]: task for task in tasks}
    records = _read_jsonl(source)
    for record in records:
        task = task_by_id.get(record["task_id"])
        if task is None:
            raise ValueError(f"task {record['task_id']!r} is absent from the selected task set")
        record["score"] = score_record(record, task["expect"])
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    raw_path = output_dir / f"rescored-{run_id}.jsonl"
    raw_path.write_text("".join(json.dumps(record, ensure_ascii=False, default=str) + "\n" for record in records), encoding="utf-8")
    summary = _summary(
        records,
        metadata={
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "offline_rescore",
            "source": str(source),
            "source_sha256": _sha256(source),
            "task_set_sha256": _sha256(task_path),
        },
        raw_name=raw_path.name,
    )
    summary_path = output_dir / f"summary-{run_id}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failures"] else 0


async def _snapshot(repo: AfterSalesRepository, *, thread_id: str, user_id: str) -> tuple[dict | None, list[dict], list[dict]]:
    case = await repo.get_case_by_thread(thread_id, user_id=user_id)
    if case is None:
        return None, [], []
    actions = await repo.list_case_actions(case["id"], user_id=user_id)
    events = await repo.list_events(case["id"], user_id=user_id)
    return case, [action.model_dump(mode="json") for action in actions], events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N tasks")
    parser.add_argument("--task-id", action="append", help="Run only the selected task ID; repeat for multiple tasks")
    parser.add_argument("--routing", choices=("after-sales", "generic"), default="after-sales")
    parser.add_argument("--tasks", type=Path, default=TASKS)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--offline", type=Path, help="Rescore an existing JSONL without model calls")
    parser.add_argument("--experiment-id", help="Resume or name an isolated experiment directory")
    args = parser.parse_args()
    tasks = json.loads(args.tasks.read_text(encoding="utf-8"))
    if args.task_id:
        selected = set(args.task_id)
        tasks = [task for task in tasks if task["id"] in selected]
        missing = selected - {task["id"] for task in tasks}
        if missing:
            parser.error(f"unknown task IDs: {', '.join(sorted(missing))}")
    if args.limit:
        tasks = tasks[: args.limit]
    if args.offline:
        return _offline(args.offline, tasks, args.tasks, args.output_dir)

    experiment_id = args.experiment_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    experiment_dir = args.output_dir / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=bool(args.experiment_id))
    raw_path = experiment_dir / "runs.jsonl"
    database = experiment_dir / "agent-eval.db"
    _async(init_engine("sqlite", url=f"sqlite+aiosqlite:///{database}", sqlite_dir=str(experiment_dir)))
    repo = AfterSalesRepository(get_session_factory())
    config = get_app_config()
    model = config.models[0].name if config.models else "unknown"
    client = DeerFlowClient(
        model_name=model,
        thinking_enabled=False,
        checkpointer=InMemorySaver(),
        available_skills=AFTER_SALES_SKILLS if args.routing == "after-sales" else None,
    )
    records = _read_jsonl(raw_path) if raw_path.exists() else []
    completed = {(record["task_id"], record["attempt"]) for record in records}
    try:
        for task in tasks:
            for attempt in range(1, args.repeat + 1):
                if (task["id"], attempt) in completed:
                    continue
                user_id = f"agent-eval-{experiment_id}-{task['id'].lower()}-{attempt}"
                thread_id = f"{user_id}-{uuid.uuid4().hex[:8]}"
                token = set_current_user(SimpleNamespace(id=user_id, system_role="user"))
                started = time.perf_counter()
                tool_calls: dict[str, dict] = {}
                tool_started: dict[str, float] = {}
                usage_by_message: dict[str, dict] = {}
                end_usage = None
                output_parts: list[str] = []
                error = None
                try:
                    for event in client.stream(_route_agent_input(task["input"], args.routing), thread_id=thread_id):
                        if event.type == "end":
                            raw_usage = event.data.get("usage") if isinstance(event.data, dict) else None
                            if raw_usage:
                                end_usage = {
                                    "input_tokens": int(raw_usage.get("input_tokens", 0) or 0),
                                    "output_tokens": int(raw_usage.get("output_tokens", 0) or 0),
                                    "total_tokens": int(raw_usage.get("total_tokens", 0) or 0),
                                }
                            continue
                        if event.type != "messages-tuple":
                            continue
                        data = event.data
                        if data.get("type") == "ai":
                            for index, call in enumerate(data.get("tool_calls", [])):
                                if not call.get("name"):
                                    continue
                                call_id = call.get("id") or f"{data.get('id', 'message')}:{index}:{call.get('name')}"
                                if call_id not in tool_calls:
                                    tool_calls[call_id] = {
                                        "id": call_id,
                                        "name": call.get("name"),
                                        "args": call.get("args"),
                                        "started_at": datetime.now(UTC).isoformat(),
                                    }
                                    tool_started[call_id] = time.perf_counter()
                            if data.get("content"):
                                output_parts.append(str(data["content"]))
                        elif data.get("type") == "tool":
                            call_id = str(data.get("tool_call_id") or "")
                            if call_id in tool_calls:
                                tool_calls[call_id]["completed"] = True
                                tool_calls[call_id]["error"] = _tool_result_error(data.get("content", ""))
                                tool_calls[call_id]["latency_ms"] = round((time.perf_counter() - tool_started[call_id]) * 1000)
                        usage = data.get("usage_metadata")
                        if usage and data.get("id"):
                            usage_by_message[data["id"]] = {
                                "input_tokens": int(usage.get("input_tokens", 0) or 0),
                                "output_tokens": int(usage.get("output_tokens", 0) or 0),
                                "total_tokens": int(usage.get("total_tokens", 0) or 0),
                            }
                except Exception as exc:  # noqa: BLE001 - failures are evaluation data
                    error = f"{type(exc).__name__}: {exc}"
                finally:
                    reset_current_user(token)
                case, actions, events = _async(_snapshot(repo, thread_id=thread_id, user_id=user_id))
                usage = end_usage
                if usage is None and usage_by_message:
                    usage = {field: sum(item[field] for item in usage_by_message.values()) for field in ("input_tokens", "output_tokens", "total_tokens")}
                recorded_tool_calls = [call for call in tool_calls.values() if call.get("name")]
                record = {
                    "experiment_id": experiment_id,
                    "entrypoint": "DeerFlowClient.stream",
                    "task_id": task["id"],
                    "attempt": attempt,
                    "split": task.get("split", "historical"),
                    "capability": task.get("capability"),
                    "model": model,
                    "config_version": config.config_version,
                    "run_at": datetime.now(UTC).isoformat(),
                    "input": task["input"],
                    "routing": args.routing,
                    "thread_id": thread_id,
                    "tool_calls": recorded_tool_calls,
                    "tool_names": [call["name"] for call in recorded_tool_calls],
                    "tool_count": len(recorded_tool_calls),
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "token_usage": usage,
                    "output": "".join(output_parts),
                    "terminal_case": case,
                    "terminal_actions": actions,
                    "terminal_events": events,
                    "error": error,
                }
                record["score"] = score_record(record, task["expect"])
                records.append(record)
                with raw_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                    stream.flush()
                print(f"{task['id']} #{attempt}: {'error' if error else (case or {}).get('status', 'no_case')}", flush=True)
    finally:
        _async(close_engine())

    summary = _summary(
        records,
        metadata={
            "generated_at": datetime.now(UTC).isoformat(),
            "experiment_id": experiment_id,
            "entrypoint": "DeerFlowClient.stream",
            "model": model,
            "config_version": config.config_version,
            "task_count": len(tasks),
            "repeats": args.repeat,
            "routing": args.routing,
            "task_set_sha256": _sha256(args.tasks),
            "evaluator_sha256": _sha256(Path(__file__)),
            "workflow_input_sha256": _workflow_input_hashes(),
        },
        raw_name=raw_path.name,
    )
    (experiment_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
