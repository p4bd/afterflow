"""SQL repository for AfterFlow cases, actions, and append-only audit events."""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.after_sales.model import ActionRequestRow, CaseEventRow, ServiceCaseRow

from .actions import ActionRequest


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
        order_id: str,
        issue_type: str,
        evidence: dict,
        decision: dict,
    ) -> dict:
        row = ServiceCaseRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            thread_id=thread_id,
            order_id=order_id,
            issue_type=issue_type,
            status="decided",
            evidence_json=evidence,
            decision_json=decision,
        )
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    async def get_case(self, case_id: str, *, user_id: str | None) -> dict | None:
        async with self._sf() as session:
            query = select(ServiceCaseRow).where(ServiceCaseRow.id == case_id)
            if user_id is not None:
                query = query.where(ServiceCaseRow.user_id == user_id)
            row = await session.scalar(query)
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
        async with self._sf() as session:
            case_exists = await session.scalar(select(ServiceCaseRow.id).where(ServiceCaseRow.id == case_id, ServiceCaseRow.user_id == user_id))
            if case_exists is None:
                raise KeyError("case not found")
            last = await session.scalar(
                select(CaseEventRow)
                .where(CaseEventRow.case_id == case_id)
                .order_by(CaseEventRow.seq.desc())
                .limit(1)
            )
            prev_hash = last.event_hash if last is not None else ""
            # Monotonic per-case sequence: deterministic ordering independent of
            # uuid4/created_at ties (SQLite does not auto-fill non-PK integers).
            seq = (last.seq + 1) if last is not None else 1
            metadata = metadata or {}
            now = datetime.now(UTC)
            # Hash covers the audit-relevant fields (including trace linkage),
            # so rewriting a trace_id or run_id also breaks the chain. created_at
            # is intentionally excluded: SQLite's datetime round-trip is not
            # canonical, and it would produce false breaks.
            payload = (
                f"{prev_hash}:{case_id}:{event_type}:{actor}:{run_id or ''}:{trace_id or ''}:"
                f"{json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
            )
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
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    async def verify_event_chain(self, case_id: str) -> dict:
        """Verify the tamper-evident audit hash chain for a case.

        Returns {"valid": bool, "broken_at": index-or-None, "count": N}.
        Any modification to a past event's type/actor/metadata changes its
        hash, which breaks the next event's prev_hash reference.
        """
        async with self._sf() as session:
            rows = (
                await session.scalars(
                    select(CaseEventRow)
                    .where(CaseEventRow.case_id == case_id)
                    .order_by(CaseEventRow.seq)
                )
            ).all()
        prev = ""
        for index, row in enumerate(rows):
            payload = (
                f"{prev}:{row.case_id}:{row.event_type}:{row.actor}:{row.run_id or ''}:{row.trace_id or ''}:"
                f"{json.dumps(row.event_metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
            )
            expected = hashlib.sha256(payload.encode()).hexdigest()
            if row.prev_hash != prev or row.event_hash != expected:
                return {"valid": False, "broken_at": index, "count": len(rows)}
            prev = row.event_hash
        return {"valid": True, "broken_at": None, "count": len(rows)}

    async def list_events(self, case_id: str, *, user_id: str) -> list[dict]:
        async with self._sf() as session:
            rows = await session.scalars(
                select(CaseEventRow).join(ServiceCaseRow, ServiceCaseRow.id == CaseEventRow.case_id).where(CaseEventRow.case_id == case_id, ServiceCaseRow.user_id == user_id).order_by(CaseEventRow.created_at, CaseEventRow.id)
            )
            return [row.to_dict() for row in rows]
