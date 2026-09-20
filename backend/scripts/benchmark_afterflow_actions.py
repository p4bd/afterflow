"""Compare the former approval-list N+1 query with the joined repository path."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.after_sales.actions import create_refund_action
from app.after_sales.repository import AfterSalesRepository
from app.after_sales.schemas import DecisionResult, Eligibility, ResolutionAction, RiskLevel
from deerflow.persistence.base import Base


def _percentile(values: list[float], fraction: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]


async def _seed(repo: AfterSalesRepository, count: int) -> None:
    decision = DecisionResult(
        eligibility=Eligibility.ELIGIBLE_WITH_APPROVAL,
        action=ResolutionAction.REFUND_ORIGINAL_PAYMENT,
        refund_amount=1,
        risk_level=RiskLevel.MEDIUM,
        reason_code="BENCHMARK",
        approval_required=True,
        approval_reasons=["benchmark"],
        policy_refs=["BENCHMARK@1"],
    )
    for index in range(count):
        case = await repo.create_case(
            user_id=f"bench-{index}",
            thread_id=None,
            order_id="ORDER-1001",
            issue_type="delivery_not_received",
            evidence={},
            decision=decision.model_dump(mode="json"),
        )
        await repo.create_action(
            create_refund_action(
                case_id=case["id"],
                order_id="ORDER-1001",
                requested_by=f"bench-{index}",
                decision=decision,
            ),
            user_id=f"bench-{index}",
        )


async def _run(args) -> dict:
    with tempfile.TemporaryDirectory(prefix="afterflow-bench-") as directory:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(directory) / 'bench.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        repo = AfterSalesRepository(async_sessionmaker(engine, expire_on_commit=False))
        await _seed(repo, args.rows)
        query_count = 0

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _count_queries(*_):
            nonlocal query_count
            query_count += 1

        async def old_path() -> int:
            actions = await repo.list_actions(limit=args.rows)
            cases = [await repo.get_case(action.case_id, user_id=None) for action in actions]
            return len(cases)

        async def joined_path() -> int:
            return len(await repo.list_actions_with_cases(limit=args.rows))

        observations = []
        for name, operation in (("n_plus_one", old_path), ("joined", joined_path)):
            await operation()
            for round_number in range(1, args.rounds + 1):
                for sample in range(1, args.samples + 1):
                    before = query_count
                    started = time.perf_counter()
                    error = None
                    try:
                        returned = await operation()
                    except Exception as exc:  # noqa: BLE001 - errors are benchmark data
                        returned = 0
                        error = f"{type(exc).__name__}: {exc}"
                    observations.append(
                        {
                            "strategy": name,
                            "round": round_number,
                            "sample": sample,
                            "latency_ms": (time.perf_counter() - started) * 1000,
                            "sql_queries": query_count - before,
                            "returned": returned,
                            "error": error,
                        }
                    )
        await engine.dispose()

    summary = {}
    for name in ("n_plus_one", "joined"):
        rows = [item for item in observations if item["strategy"] == name]
        latencies = [item["latency_ms"] for item in rows]
        summary[name] = {
            "observations": len(rows),
            "p50_ms": statistics.median(latencies),
            "p95_ms": _percentile(latencies, 0.95),
            "mean_sql_queries": statistics.mean(item["sql_queries"] for item in rows),
            "errors": sum(item["error"] is not None for item in rows),
        }
    return {
        "scope": "local SQLite repository approval-list query only; no HTTP, model, payment, or logistics latency",
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "config": {"rows": args.rows, "samples_per_round": args.samples, "rounds": args.rounds, "warmups": 1},
        "summary": summary,
        "observations": observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return int(any(item["errors"] for item in result["summary"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
