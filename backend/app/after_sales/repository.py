"""SQL repository for AfterFlow cases, actions, and append-only audit events."""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.after_sales.model import ActionRequestRow, CaseEventRow, ServiceCaseRow

from .actions import ActionRequest, ActionStatus


class ConcurrentActionError(RuntimeError):
    """An action changed or a conflicting action is already active."""


async def release_expired_reservations(repository: "AfterSalesRepository", executor, *, now: datetime | None = None) -> int:
    """Release frozen funds for approvals that expired without executing.

    Authorize-then-capture is only safe if every authorization is eventually
    captured or released. This reaper returns the frozen amount to the
    available pool for each expired reserved action and flips its flag.
    In production this runs on a cron; the approval-desk load also calls it.
    """
    from .actions import get_mock_refund_executor

    executor = executor if executor is not None else get_mock_refund_executor()
    expired = await repository.list_expired_reserved_actions(now=now or datetime.now(UTC))
    released = 0
    for action, order_id in expired:
        executor.release(order_id=order_id, amount=action.payload["amount"])
        await repository.save_action(
            action.model_copy(update={"reserved": False}),
            user_id=None,
            expected_version=action.version,
        )
        released += 1
    return released


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _to_action(row: ActionRequestRow) -> ActionRequest:
    data = row.to_dict()
    data["approved_at"] = _aware(data["approved_at"])
    data["expires_at"] = _aware(data["expires_at"])
    return ActionRequest.model_validate(data)


class AfterSalesRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create_case(
        self,
        *,
        user_id: str,
        thread_id: str | None,
        order_id: str | None,
        issue_type: str | None,
        evidence: dict,
        decision: dict,
        complaint_text: str = "",
        customer_expectation: str | None = None,
        status: str = "decided",
        next_step: str = "review_decision",
        reply_draft: str = "",
    ) -> dict:
        row = ServiceCaseRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            thread_id=thread_id,
            order_id=order_id,
            issue_type=issue_type,
            status=status,
            complaint_text=complaint_text,
            customer_expectation=customer_expectation,
            next_step=next_step,
            reply_draft=reply_draft,
            evidence_json=evidence,
            decision_json=decision,
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    async def list_cases(self, *, user_id: str, limit: int = 100) -> list[dict]:
        async with self._sf() as session:
            rows = await session.scalars(select(ServiceCaseRow).where(ServiceCaseRow.user_id == user_id).order_by(ServiceCaseRow.updated_at.desc(), ServiceCaseRow.id.desc()).limit(limit))
            return [row.to_dict() for row in rows]

    async def update_case(self, case_id: str, *, user_id: str, expected_version: int, values: dict) -> dict:
        values = {**values, "version": expected_version + 1, "updated_at": datetime.now(UTC)}
        async with self._sf() as session:
            result = await session.execute(
                update(ServiceCaseRow)
                .where(
                    ServiceCaseRow.id == case_id,
                    ServiceCaseRow.user_id == user_id,
                    ServiceCaseRow.version == expected_version,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                await session.rollback()
                raise ConcurrentActionError("case version conflict")
            await session.commit()
        saved = await self.get_case(case_id, user_id=user_id)
        if saved is None:
            raise ConcurrentActionError("case disappeared after update")
        return saved

    async def get_case(self, case_id: str, *, user_id: str | None) -> dict | None:
        async with self._sf() as session:
            query = select(ServiceCaseRow).where(ServiceCaseRow.id == case_id)
            if user_id is not None:
                query = query.where(ServiceCaseRow.user_id == user_id)
            row = await session.scalar(query)
            return row.to_dict() if row else None

    async def get_case_by_thread(self, thread_id: str, *, user_id: str) -> dict | None:
        async with self._sf() as session:
            row = await session.scalar(
                select(ServiceCaseRow).where(
                    ServiceCaseRow.thread_id == thread_id,
                    ServiceCaseRow.user_id == user_id,
                )
            )
            return row.to_dict() if row else None

    async def create_action(self, action: ActionRequest, *, user_id: str) -> ActionRequest:
        async with self._sf() as session:
            case_exists = await session.scalar(select(ServiceCaseRow.id).where(ServiceCaseRow.id == action.case_id, ServiceCaseRow.user_id == user_id))
            if case_exists is None:
                raise KeyError("case not found")
            active = await session.scalar(
                select(ActionRequestRow.id).where(
                    ActionRequestRow.case_id == action.case_id,
                    ActionRequestRow.status.in_(("pending_approval", "approved")),
                )
            )
            if active is not None:
                raise ConcurrentActionError("case already has an active action")
            row = ActionRequestRow(**action.model_dump())
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as error:
                # DB-level partial unique index backstops a TOCTOU race where
                # two concurrent requests both passed the select above.
                await session.rollback()
                raise ConcurrentActionError("case already has an active action") from error
            await session.refresh(row)
            return _to_action(row)

    async def get_action(self, action_id: str, *, user_id: str | None) -> ActionRequest | None:
        async with self._sf() as session:
            query = select(ActionRequestRow).where(ActionRequestRow.id == action_id)
            if user_id is not None:
                query = query.join(ServiceCaseRow, ServiceCaseRow.id == ActionRequestRow.case_id).where(ServiceCaseRow.user_id == user_id)
            row = await session.scalar(query)
            return _to_action(row) if row else None

    async def list_actions(self, *, status: str | None = None, limit: int = 100) -> list[ActionRequest]:
        query = select(ActionRequestRow).order_by(ActionRequestRow.expires_at).limit(limit)
        if status is not None:
            query = query.where(ActionRequestRow.status == status)
        async with self._sf() as session:
            rows = await session.scalars(query)
            return [_to_action(row) for row in rows]

    async def list_actions_with_cases(self, *, status: str | None = None, limit: int = 100) -> list[tuple[ActionRequest, dict]]:
        query = select(ActionRequestRow, ServiceCaseRow).join(ServiceCaseRow, ServiceCaseRow.id == ActionRequestRow.case_id).order_by(ActionRequestRow.expires_at).limit(limit)
        if status is not None:
            query = query.where(ActionRequestRow.status == status)
        async with self._sf() as session:
            rows = await session.execute(query)
            return [(_to_action(action), case.to_dict()) for action, case in rows.all()]

    async def list_case_actions(self, case_id: str, *, user_id: str) -> list[ActionRequest]:
        async with self._sf() as session:
            rows = await session.scalars(
                select(ActionRequestRow).join(ServiceCaseRow, ServiceCaseRow.id == ActionRequestRow.case_id).where(ActionRequestRow.case_id == case_id, ServiceCaseRow.user_id == user_id).order_by(ActionRequestRow.expires_at.desc())
            )
            return [_to_action(row) for row in rows]

    async def invalidate_active_actions(self, case_id: str, *, user_id: str, reason: str) -> list[ActionRequest]:
        actions = await self.list_case_actions(case_id, user_id=user_id)
        invalidated: list[ActionRequest] = []
        for action in actions:
            if action.status.value not in {"pending_approval", "approved"}:
                continue
            invalidated.append(
                await self.save_action(
                    action.model_copy(update={"status": ActionStatus.REJECTED, "comment": reason, "version": action.version + 1}),
                    user_id=user_id,
                    expected_version=action.version,
                )
            )
        return invalidated

    async def list_expired_reserved_actions(self, *, now: datetime) -> list[tuple[ActionRequest, str]]:
        """Approved/partial actions that froze funds but never executed in time.

        Returns (action, order_id) pairs so the reaper can release the exact
        reservation. These are the leak source the critic flagged: without a
        reaper, reserved money accumulates and available balance drains.
        """
        query = (
            select(ActionRequestRow, ServiceCaseRow.order_id)
            .join(ServiceCaseRow, ServiceCaseRow.id == ActionRequestRow.case_id)
            .where(
                ActionRequestRow.reserved == 1,
                ActionRequestRow.status.in_(("pending_approval", "approved")),
                ActionRequestRow.expires_at < now,
            )
        )
        async with self._sf() as session:
            rows = await session.execute(query)
            return [(_to_action(row), order_id) for row, order_id in rows.all()]

    async def save_action(self, action: ActionRequest, *, user_id: str | None, expected_version: int) -> ActionRequest:
        values = action.model_dump(exclude={"id", "case_id"})
        conditions = [ActionRequestRow.id == action.id, ActionRequestRow.version == expected_version]
        if user_id is not None:
            conditions.append(ActionRequestRow.case_id.in_(select(ServiceCaseRow.id).where(ServiceCaseRow.user_id == user_id)))
        async with self._sf() as session:
            result = await session.execute(update(ActionRequestRow).where(*conditions).values(**values))
            if result.rowcount != 1:
                await session.rollback()
                raise ConcurrentActionError("action version conflict")
            await session.commit()
        saved = await self.get_action(action.id, user_id=user_id)
        if saved is None:
            raise ConcurrentActionError("action disappeared after update")
        return saved

    async def append_event(
        self,
        *,
        case_id: str,
        user_id: str,
        actor: str,
        event_type: str,
        run_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Append a tamper-evident event to the case chain.

        P0-B: the (case_id, seq) DB-level UNIQUE index added in migration
        ``0011_case_event_seq_unique`` is the authoritative race resolution.
        Without it, two concurrent callers reading the same ``last`` row
        both compute ``last.seq + 1`` and both INSERT, forking the chain.
        With it, the second INSERT raises ``IntegrityError``; we re-read
        ``last`` and retry. The retry is bounded (3 attempts) — beyond that
        we surface the integrity failure to the caller rather than spin.
        """
        metadata = metadata or {}
        # Bounded retry on (case_id, seq) conflict. Each attempt re-reads
        # ``last`` inside its own transaction so a concurrent writer who
        # wins the race cannot leave us with a stale ``last`` and a doomed
        # computed seq.
        for attempt in range(3):
            async with self._sf() as session:
                case_exists = await session.scalar(select(ServiceCaseRow.id).where(ServiceCaseRow.id == case_id, ServiceCaseRow.user_id == user_id))
                if case_exists is None:
                    raise KeyError("case not found")
                last = await session.scalar(select(CaseEventRow).where(CaseEventRow.case_id == case_id).order_by(CaseEventRow.seq.desc()).limit(1))
                prev_hash = last.event_hash if last is not None else ""
                # Monotonic per-case sequence: deterministic ordering
                # independent of uuid4/created_at ties (SQLite does not
                # auto-fill non-PK integers).
                seq = (last.seq + 1) if last is not None else 1
                now = datetime.now(UTC)
                # Hash covers the audit-relevant fields (including trace
                # linkage), so rewriting a trace_id or run_id also breaks
                # the chain. created_at is intentionally excluded: SQLite's
                # datetime round-trip is not canonical, and it would
                # produce false breaks.
                payload = f"{prev_hash}:{case_id}:{event_type}:{actor}:{run_id or ''}:{trace_id or ''}:{json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
                event_hash = hashlib.sha256(payload.encode()).hexdigest()
                row = CaseEventRow(
                    id=str(uuid.uuid4()),
                    case_id=case_id,
                    actor=actor,
                    event_type=event_type,
                    run_id=run_id,
                    trace_id=trace_id,
                    event_metadata=metadata,
                    prev_hash=prev_hash,
                    event_hash=event_hash,
                    created_at=now,
                    seq=seq,
                )
                session.add(row)
                try:
                    await session.commit()
                except IntegrityError:
                    # Concurrent append_event won the seq race for this
                    # case. Roll back, re-read last (a different writer
                    # has now committed), and try again with a fresh seq.
                    await session.rollback()
                    if attempt == 2:
                        # Out of retries: surface as a ConcurrentActionError
                        # rather than swallowing or letting an opaque
                        # IntegrityError leak into the API layer.
                        raise ConcurrentActionError("case_event seq collision persisted across 3 append_event attempts") from None
                    continue
                await session.refresh(row)
                return row.to_dict()
        # Unreachable: the loop either returns or raises on attempt 2.
        raise ConcurrentActionError("append_event exhausted retry loop without committing")

    async def verify_event_chain(self, case_id: str) -> dict:
        """Verify the tamper-evident audit hash chain for a case.

        Returns {"valid": bool, "broken_at": index-or-None, "count": N}.
        Any modification to a past event's type/actor/metadata changes its
        hash, which breaks the next event's prev_hash reference.
        """
        async with self._sf() as session:
            rows = (await session.scalars(select(CaseEventRow).where(CaseEventRow.case_id == case_id).order_by(CaseEventRow.seq))).all()
        prev = ""
        for index, row in enumerate(rows):
            payload = f"{prev}:{row.case_id}:{row.event_type}:{row.actor}:{row.run_id or ''}:{row.trace_id or ''}:{json.dumps(row.event_metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
            expected = hashlib.sha256(payload.encode()).hexdigest()
            if row.prev_hash != prev or row.event_hash != expected:
                return {"valid": False, "broken_at": index, "count": len(rows)}
            prev = row.event_hash
        return {"valid": True, "broken_at": None, "count": len(rows)}

    async def list_events(self, case_id: str, *, user_id: str) -> list[dict]:
        async with self._sf() as session:
            rows = await session.scalars(select(CaseEventRow).join(ServiceCaseRow, ServiceCaseRow.id == CaseEventRow.case_id).where(CaseEventRow.case_id == case_id, ServiceCaseRow.user_id == user_id).order_by(CaseEventRow.seq))
            return [row.to_dict() for row in rows]
