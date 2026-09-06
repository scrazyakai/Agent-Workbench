import hashlib
import json
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.db.models import AgentVersion, Run, RunEvent, utcnow
from app.schemas.runs import RunCreate, RunEventRead, RunRead, RunSummary

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def request_fingerprint(data: RunCreate) -> str:
    canonical = json.dumps(
        {
            "target": data.target.model_dump(mode="json"),
            "thread_id": data.thread_id,
            "input": data.input,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def append_event(session: Session, run: Run, event_type: str, payload: dict | None = None):
    sequence = session.scalar(
        select(func.coalesce(func.max(RunEvent.sequence), 0)).where(RunEvent.run_id == run.id)
    )
    event = RunEvent(
        run_id=run.id,
        workspace_id=run.workspace_id,
        sequence=sequence + 1,
        event_type=event_type,
        payload=payload or {},
    )
    session.add(event)
    return event


class RunService:
    def __init__(self, session: Session, workspace_id: str):
        self.session = session
        self.workspace_id = workspace_id

    def get(self, run_id: UUID, *, lock=False):
        query = select(Run).where(Run.id == run_id, Run.workspace_id == self.workspace_id)
        if lock:
            query = query.with_for_update()
        run = self.session.scalar(query)
        if run is None:
            raise DomainError(404, "run_not_found", "Run not found")
        return run

    @staticmethod
    def summary(run: Run):
        return RunSummary(
            id=run.id,
            workspace_id=run.workspace_id,
            target={"type": "agent", "id": run.agent_id, "version": run.agent_version},
            thread_id=run.thread_id,
            status=run.status,
            execution_attempts=run.execution_attempts,
            recovery_count=run.recovery_count,
            created_at=run.created_at,
            updated_at=run.updated_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )

    @classmethod
    def serialize(cls, run: Run):
        return RunRead(
            **cls.summary(run).model_dump(),
            input=run.input,
            result=run.result,
            error=run.error,
            cancel_requested_at=run.cancel_requested_at,
        )

    def create(self, data: RunCreate):
        fingerprint = request_fingerprint(data)
        if data.idempotency_key:
            existing = self.session.scalar(
                select(Run).where(
                    Run.workspace_id == self.workspace_id,
                    Run.idempotency_key == data.idempotency_key,
                )
            )
            if existing is not None:
                return self._resolve_idempotent(existing, fingerprint)

        version = self.session.scalar(
            select(AgentVersion).where(
                AgentVersion.workspace_id == self.workspace_id,
                AgentVersion.agent_id == data.target.id,
                AgentVersion.version == data.target.version,
            )
        )
        if version is None:
            raise DomainError(404, "agent_version_not_found", "Published Agent version not found")

        run = Run(
            workspace_id=self.workspace_id,
            agent_id=version.agent_id,
            agent_version_id=version.id,
            agent_version=version.version,
            thread_id=data.thread_id,
            idempotency_key=data.idempotency_key,
            request_fingerprint=fingerprint if data.idempotency_key else None,
            input=data.input,
            status="queued",
        )
        self.session.add(run)
        try:
            self.session.flush()
            append_event(self.session, run, "run_queued", {"status": "queued"})
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            if not data.idempotency_key:
                raise DomainError(409, "run_conflict", "Run could not be created") from exc
            existing = self.session.scalar(
                select(Run).where(
                    Run.workspace_id == self.workspace_id,
                    Run.idempotency_key == data.idempotency_key,
                )
            )
            if existing is None:
                raise DomainError(409, "run_conflict", "Run could not be created") from exc
            return self._resolve_idempotent(existing, fingerprint)
        return self.serialize(run)

    def _resolve_idempotent(self, run: Run, fingerprint: str):
        if run.request_fingerprint != fingerprint:
            raise DomainError(
                409,
                "idempotency_conflict",
                "Idempotency key was already used for a different request",
            )
        return self.serialize(run)

    def list(self, offset, limit, status=None, target_id=None, thread_id=None):
        filters = [Run.workspace_id == self.workspace_id]
        if status is not None:
            filters.append(Run.status == status)
        if target_id is not None:
            filters.append(Run.agent_id == target_id)
        if thread_id is not None:
            filters.append(Run.thread_id == thread_id)
        total = self.session.scalar(select(func.count()).select_from(Run).where(*filters))
        rows = self.session.scalars(
            select(Run)
            .where(*filters)
            .order_by(Run.created_at.desc(), Run.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return {
            "items": [self.summary(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def events(self, run_id: UUID, after: int, limit: int):
        self.get(run_id)
        rows = list(
            self.session.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.sequence > after)
                .order_by(RunEvent.sequence)
                .limit(limit + 1)
            )
        )
        has_more = len(rows) > limit
        visible = rows[:limit]
        return {
            "items": [RunEventRead.model_validate(row, from_attributes=True) for row in visible],
            "next_cursor": visible[-1].sequence if visible else after,
            "has_more": has_more,
        }

    def cancel(self, run_id: UUID):
        run = self.get(run_id, lock=True)
        now = utcnow()
        if run.status == "queued":
            run.status = "cancelled"
            run.cancel_requested_at = now
            run.completed_at = now
            run.updated_at = now
            append_event(self.session, run, "run_cancelled", {"status": "cancelled"})
        elif run.status == "running":
            run.status = "cancelling"
            run.cancel_requested_at = now
            run.updated_at = now
            append_event(self.session, run, "run_cancelling", {"status": "cancelling"})
        elif run.status not in {"cancelling", "cancelled"}:
            raise DomainError(409, "run_not_cancellable", "Run is already complete")
        self.session.commit()
        return self.serialize(run)
