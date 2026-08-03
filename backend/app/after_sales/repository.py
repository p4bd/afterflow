"""SQL repository for AfterFlow cases, actions, and append-only audit events."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.after_sales.model import ActionRequestRow, CaseEventRow, ServiceCaseRow

from .actions import ActionRequest


class ConcurrentActionError(RuntimeError):
    """An action changed or a conflicting action is already active."""


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
            await session.commit()
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
            row = CaseEventRow(
                id=str(uuid.uuid4()),
                case_id=case_id,
                actor=actor,
                event_type=event_type,
                run_id=run_id,
                trace_id=trace_id,
                event_metadata=metadata or {},
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.to_dict()

    async def list_events(self, case_id: str, *, user_id: str) -> list[dict]:
        async with self._sf() as session:
            rows = await session.scalars(
                select(CaseEventRow).join(ServiceCaseRow, ServiceCaseRow.id == CaseEventRow.case_id).where(CaseEventRow.case_id == case_id, ServiceCaseRow.user_id == user_id).order_by(CaseEventRow.created_at, CaseEventRow.id)
            )
            return [row.to_dict() for row in rows]
