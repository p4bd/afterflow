"""P0-B: concurrent append_event must not fork the audit hash chain.

The DB-level ``UNIQUE(case_id, seq)`` constraint added in migration
``0011_case_event_seq_unique`` is the authoritative race resolution: two
concurrent ``append_event`` calls for the same case cannot both INSERT the
same ``seq``. The repository catches ``IntegrityError`` and retries with a
fresh seq read. After any number of concurrent appenders, the chain must
verify cleanly via ``verify_event_chain``.

These tests cover three angles:

1. The retry path actually fires under concurrent load and produces a
   monotonically increasing seq with no duplicates.
2. ``verify_event_chain`` returns ``valid=True`` after concurrent writes
   — no orphaned branch is silently swallowed.
3. The 0011 migration applies cleanly on a fresh DB via
   ``Base.metadata.create_all`` — the SQLAlchemy model emits the composite
   unique constraint, so a test that does NOT run alembic still gets the
   P0-B invariant.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.after_sales.repository import AfterSalesRepository
from app.after_sales.schemas import DecisionResult, Eligibility, ResolutionAction, RiskLevel
from deerflow.persistence.after_sales.model import CaseEventRow, ServiceCaseRow
from deerflow.persistence.base import Base


def _decision() -> DecisionResult:
    return DecisionResult(
        eligibility=Eligibility.ELIGIBLE_WITH_APPROVAL,
        action=ResolutionAction.REFUND_ORIGINAL_PAYMENT,
        refund_amount=90_900,
        risk_level=RiskLevel.MEDIUM,
        reason_code="POLICY_MATCHED",
        approval_required=True,
        approval_reasons=["exceeds_operator_limit"],
        policy_refs=["AFTER-SALES-CN@2026.07"],
    )


@pytest_asyncio.fixture
async def repository(tmp_path):
    db_path = tmp_path / "hash_chain_concurrent.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield AfterSalesRepository(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


@pytest_asyncio.fixture
async def case(repository):
    return await repository.create_case(
        user_id="owner-1",
        thread_id=None,
        order_id="ORDER-P0B",
        issue_type="delivery_not_received",
        evidence={},
        decision=_decision().model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# Test 1: two concurrent append_event for the same case both succeed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_append_event_produces_strictly_monotonic_seq(repository, case):
    """N parallel appends → N rows with seq=1..N, all distinct.

    Without the composite UNIQUE(case_id, seq), two callers reading the
    same ``last`` row could each compute ``last.seq + 1`` and both
    INSERT. With the composite UNIQUE plus the IntegrityError retry,
    every append lands on a unique per-case seq.
    """
    n = 8

    async def append(i: int) -> dict:
        return await repository.append_event(
            case_id=case["id"],
            user_id="owner-1",
            actor=f"agent-{i}",
            event_type=f"event_{i}",
        )

    results = await asyncio.gather(*(append(i) for i in range(n)))

    seqs = sorted(r["seq"] for r in results)
    assert seqs == list(range(1, n + 1)), f"concurrent append_event must produce seq=1..{n} (no gaps, no duplicates); got {seqs}"


@pytest.mark.asyncio
async def test_concurrent_append_event_with_shared_barrier_all_succeed(repository, case):
    """Release all concurrent appenders at once via an asyncio.Event so
    they all observe the same ``last`` row — the worst case for the seq
    race. Every append must still produce a unique seq and the chain
    must verify.
    """
    await repository.append_event(case_id=case["id"], user_id="owner-1", actor="seed", event_type="seed")

    n = 6
    barrier = asyncio.Event()

    async def append_after_barrier(i: int) -> dict:
        await barrier.wait()
        return await repository.append_event(
            case_id=case["id"],
            user_id="owner-1",
            actor=f"agent-{i}",
            event_type=f"raced_{i}",
        )

    workers = [asyncio.create_task(append_after_barrier(i)) for i in range(n)]
    barrier.set()
    results = await asyncio.gather(*workers)

    seqs = sorted(r["seq"] for r in results)
    assert seqs == list(range(2, n + 2)), f"expected seqs 2..{n + 1}, got {seqs}"


# ---------------------------------------------------------------------------
# Test 2: after concurrent append, verify_event_chain returns valid=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_append_event_chain_remains_tamper_evident(repository, case):
    """After concurrent appends, ``verify_event_chain`` returns valid=True.

    This is the P0-B regression check: without the fix, the chain forks
    and one branch is silently orphaned — locally valid, globally
    broken. With the composite UNIQUE + retry, all rows form a single
    linear chain with no gaps.
    """
    n = 12

    async def append(i: int) -> dict:
        return await repository.append_event(
            case_id=case["id"],
            user_id="owner-1",
            actor=f"agent-{i}",
            event_type=f"event_{i}",
            metadata={"index": i},
        )

    results = await asyncio.gather(*(append(i) for i in range(n)))

    outcome = await repository.verify_event_chain(case["id"])
    assert outcome["valid"] is True, f"concurrent append_event must leave the chain intact; verify_event_chain={outcome}"
    assert outcome["broken_at"] is None
    assert outcome["count"] == n
    assert len(results) == n
    seqs = [r["seq"] for r in results]
    assert len(set(seqs)) == n  # no two results share a seq


@pytest.mark.asyncio
async def test_concurrent_append_event_from_cross_threads_produces_valid_chain(tmp_path):
    """Cross-thread contention is the realistic P0-B failure mode: each
    thread opens its own engine + session_factory against the same
    SQLite file. The pre-P0-B code would let both threads INSERT the
    same seq; the post-P0-B code makes the IntegrityError retry produce
    a single linear chain.
    """
    db_file = tmp_path / "cross_thread.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    bootstrap_engine = create_async_engine(db_url)
    async with bootstrap_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bootstrap_sf = async_sessionmaker(bootstrap_engine, expire_on_commit=False)
    bootstrap_repo = AfterSalesRepository(bootstrap_sf)
    created = await bootstrap_repo.create_case(
        user_id="owner-1",
        thread_id=None,
        order_id="ORDER-XTHREAD",
        issue_type="delivery_not_received",
        evidence={},
        decision=_decision().model_dump(mode="json"),
    )
    case_id = created["id"]
    await bootstrap_engine.dispose()

    n = 6
    barrier = threading.Barrier(n)
    results: list[dict] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()
    errors_lock = threading.Lock()

    def worker(index: int) -> None:
        try:
            local_engine = create_async_engine(db_url)
            local_sf = async_sessionmaker(local_engine, expire_on_commit=False)
            local_repo = AfterSalesRepository(local_sf)

            async def go() -> dict:
                # Synchronize all threads at the same point so they all
                # observe the same ``last`` row before racing.
                barrier.wait()
                return await local_repo.append_event(
                    case_id=case_id,
                    user_id="owner-1",
                    actor=f"xthread-{index}",
                    event_type=f"xthread_event_{index}",
                )

            loop = asyncio.new_event_loop()
            try:
                outcome = loop.run_until_complete(go())
            finally:
                loop.close()
            with results_lock:
                results.append(outcome)
        except BaseException as exc:  # noqa: BLE001
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,), name=f"xt-{i}") for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"worker threads raised: {errors}"

    verify_engine = create_async_engine(db_url)
    verify_sf = async_sessionmaker(verify_engine, expire_on_commit=False)
    async with verify_sf() as verify_session:
        rows = (await verify_session.scalars(select(CaseEventRow).where(CaseEventRow.case_id == case_id).order_by(CaseEventRow.seq))).all()
    assert len(rows) == n, f"expected {n} rows after cross-thread appends, got {len(rows)}"
    seqs = [r.seq for r in rows]
    assert seqs == list(range(1, n + 1)), f"expected seqs 1..{n}, got {seqs}"

    verify_repo = AfterSalesRepository(async_sessionmaker(verify_engine, expire_on_commit=False))
    outcome = await verify_repo.verify_event_chain(case_id)
    await verify_engine.dispose()
    assert outcome["valid"] is True
    assert outcome["count"] == n


# ---------------------------------------------------------------------------
# Test 3: the migration applies cleanly on a fresh DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_event_seq_unique_constraint_present_on_fresh_db(tmp_path):
    """The SQLAlchemy model must emit the composite UNIQUE constraint, so
    a test that bootstraps via ``Base.metadata.create_all`` (rather than
    alembic upgrade) still gets the P0-B invariant.

    We verify that duplicate (case_id, seq) INSERTs raise
    ``IntegrityError``, while the same seq on a different case still
    succeeds.
    """
    db_file = tmp_path / "fresh_metadata.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    async with sf() as session:
        session.add(
            ServiceCaseRow(
                id="C-A",
                user_id="owner-1",
                thread_id=None,
                order_id="ORDER-A",
                issue_type="delivery_not_received",
                status="decided",
                evidence_json={},
                decision_json={},
            )
        )
        session.add(
            ServiceCaseRow(
                id="C-B",
                user_id="owner-1",
                thread_id=None,
                order_id="ORDER-B",
                issue_type="delivery_not_received",
                status="decided",
                evidence_json={},
                decision_json={},
            )
        )
        await session.commit()

    now = datetime(2026, 9, 3, tzinfo=UTC)

    async with sf() as session:
        session.add(
            CaseEventRow(
                id="E-A-1",
                case_id="C-A",
                event_type="seed",
                actor="seed",
                event_metadata={},
                prev_hash="",
                event_hash="h1",
                created_at=now,
                seq=1,
            )
        )
        await session.commit()

    # Duplicate (case_id, seq) must be rejected by the constraint.
    async with sf() as session:
        session.add(
            CaseEventRow(
                id="E-A-dup",
                case_id="C-A",
                event_type="dup",
                actor="seed",
                event_metadata={},
                prev_hash="",
                event_hash="h2",
                created_at=now,
                seq=1,  # collision on (case_id, seq)
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    # Different case, same seq → must succeed.
    async with sf() as session:
        session.add(
            CaseEventRow(
                id="E-B-1",
                case_id="C-B",
                event_type="seed",
                actor="seed",
                event_metadata={},
                prev_hash="",
                event_hash="h3",
                created_at=now,
                seq=1,  # different case — must NOT collide
            )
        )
        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_case_event_seq_unique_constraint_present_via_alembic(tmp_path, monkeypatch):
    """Bootstrapping via alembic (the canonical production path) must also
    yield the composite UNIQUE constraint and reject (case_id, seq)
    duplicates.

    We invoke ``alembic upgrade head`` from a thread (with its own event
    loop) so it doesn't conflict with pytest-asyncio's already-running
    loop. The DB URL is passed via a temporary alembic.ini that
    overrides ``sqlalchemy.url``.

    Note: we deliberately avoid ``subprocess`` here because
    ``tmp_path`` may contain non-ASCII characters on Windows (the user's
    username in this case) which trips the default ``gbk`` locale
    decoding in ``configparser`` when alembic reads the ini file from
    inside the subprocess.
    """
    import threading

    alembic_dir = Path(__file__).resolve().parents[1] / "packages" / "harness" / "deerflow" / "persistence" / "migrations"
    db_file = tmp_path / "alembic_path.db"
    # SQLite URLs need forward slashes on Windows.
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    error: list[BaseException] = []
    error_lock = threading.Lock()

    def upgrade_in_thread() -> None:
        try:
            from alembic import command as alembic_command
            from alembic.config import Config

            cfg = Config()
            cfg.set_main_option("script_location", str(alembic_dir))
            cfg.set_main_option("sqlalchemy.url", db_url)
            alembic_command.upgrade(cfg, "head")
        except BaseException as exc:  # noqa: BLE001
            with error_lock:
                error.append(exc)

    t = threading.Thread(target=upgrade_in_thread, name="alembic-upgrade")
    t.start()
    t.join()

    if error:
        raise error[0]

    engine = create_async_engine(db_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    async with sf() as session:
        session.add(
            ServiceCaseRow(
                id="C-A",
                user_id="owner-1",
                thread_id=None,
                order_id="ORDER-A",
                issue_type="delivery_not_received",
                status="decided",
                evidence_json={},
                decision_json={},
            )
        )
        await session.commit()

    now = datetime(2026, 9, 3, tzinfo=UTC)

    async with sf() as session:
        session.add(
            CaseEventRow(
                id="E-A-1",
                case_id="C-A",
                event_type="seed",
                actor="seed",
                event_metadata={},
                prev_hash="",
                event_hash="h1",
                created_at=now,
                seq=1,
            )
        )
        await session.commit()

    # Duplicate (case_id, seq) must be rejected.
    async with sf() as session:
        session.add(
            CaseEventRow(
                id="E-A-dup",
                case_id="C-A",
                event_type="dup",
                actor="seed",
                event_metadata={},
                prev_hash="",
                event_hash="h2",
                created_at=now,
                seq=1,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    await engine.dispose()
