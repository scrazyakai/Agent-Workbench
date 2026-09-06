import logging
import os
import socket
import time
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from sqlalchemy import exists, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.core.config import Settings
from app.db.models import AgentVersion, Checkpoint, Run, StepExecution, utcnow
from app.services.runs import append_event

logger = logging.getLogger(__name__)


class DeterministicRunError(Exception):
    def __init__(self, code: str, safe_message: str):
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


class LostLeaseError(Exception):
    pass


class RunWorker:
    def __init__(self, session_factory, settings: Settings, worker_id: str | None = None):
        self.session_factory = session_factory
        self.settings = settings
        self.workspace_id = settings.workspace_id
        self.worker_id = worker_id or (f"{socket.gethostname()}-{os.getpid()}-{str(uuid4())[:8]}")

    def recover_expired(self, now=None) -> int:
        now = now or utcnow()
        recovered = 0
        with self.session_factory() as session, session.begin():
            rows = list(
                session.scalars(
                    select(Run)
                    .where(
                        Run.workspace_id == self.workspace_id,
                        Run.status.in_(["running", "cancelling"]),
                        Run.lease_expires_at < now,
                    )
                    .order_by(Run.lease_expires_at)
                    .with_for_update(skip_locked=True)
                    .limit(100)
                )
            )
            for run in rows:
                run.worker_id = None
                run.lease_expires_at = None
                run.heartbeat_at = None
                run.updated_at = now
                if run.status == "cancelling" or run.cancel_requested_at is not None:
                    run.status = "cancelled"
                    run.completed_at = now
                    append_event(session, run, "run_cancelled", {"reason": "lease_expired"})
                else:
                    run.status = "queued"
                    run.recovery_count += 1
                    append_event(
                        session,
                        run,
                        "lease_expired",
                        {"recovery_count": run.recovery_count},
                    )
                recovered += 1
        return recovered

    def claim(self, now=None) -> UUID | None:
        now = now or utcnow()
        active = aliased(Run)
        active_same_thread = exists(
            select(active.id).where(
                active.workspace_id == Run.workspace_id,
                active.thread_id == Run.thread_id,
                active.id != Run.id,
                active.status.in_(["running", "cancelling"]),
            )
        )
        with self.session_factory() as session, session.begin():
            run = session.scalar(
                select(Run)
                .where(
                    Run.workspace_id == self.workspace_id,
                    Run.status == "queued",
                    or_(Run.thread_id.is_(None), ~active_same_thread),
                )
                .order_by(Run.created_at, Run.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if run is None:
                return None
            run.status = "running"
            run.worker_id = self.worker_id
            run.heartbeat_at = now
            run.lease_expires_at = now + timedelta(seconds=self.settings.worker_lease_seconds)
            run.started_at = run.started_at or now
            run.updated_at = now
            append_event(
                session,
                run,
                "run_resumed" if run.recovery_count else "run_started",
                {"recovery_count": run.recovery_count},
            )
            return run.id

    def run_once(self, *, max_steps: int | None = None) -> UUID | None:
        self.recover_expired()
        run_id = self.claim()
        if run_id is None:
            return None
        self.execute_claimed(run_id, max_steps=max_steps)
        return run_id

    def execute_claimed(self, run_id: UUID, *, max_steps: int | None = None):
        try:
            completed = self._completed_steps(run_id)
            executed = 0
            if "validate_input" not in completed:
                if not self._run_step(run_id, "validate_input", self._validate_input):
                    return
                executed += 1
                if max_steps is not None and executed >= max_steps:
                    return
            if "produce_result" not in completed:
                self._run_step(run_id, "produce_result", self._produce_result, final=True)
        except DeterministicRunError as exc:
            self._fail(run_id, exc.code, exc.safe_message, retryable=False)
        except LostLeaseError:
            logger.warning("worker_lost_run_lease", extra={"run_id": str(run_id)})
        except Exception:
            logger.exception("worker_run_attempt_failed", extra={"run_id": str(run_id)})
            self._fail(run_id, "execution_error", "Run execution failed", retryable=True)

    def _completed_steps(self, run_id: UUID) -> set[str]:
        with self.session_factory() as session:
            checkpoint = session.scalar(
                select(Checkpoint)
                .where(Checkpoint.run_id == run_id, Checkpoint.workspace_id == self.workspace_id)
                .order_by(Checkpoint.sequence.desc())
                .limit(1)
            )
            return set(checkpoint.state.get("completed_steps", [])) if checkpoint else set()

    def _run_step(self, run_id: UUID, step_key: str, handler, *, final=False) -> bool:
        with self.session_factory() as session, session.begin():
            run = self._locked_owned_run(session, run_id)
            if self._cancel_if_requested(session, run):
                return False
            step = session.scalar(
                select(StepExecution).where(
                    StepExecution.run_id == run.id,
                    StepExecution.step_key == step_key,
                )
            )
            if step is not None and step.status == "succeeded":
                return True
            if step is None:
                step = StepExecution(
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                    step_key=step_key,
                    status="running",
                    attempt_count=0,
                )
                session.add(step)
            step.status = "running"
            step.attempt_count += 1
            step.updated_at = utcnow()
            self._renew(run)
            append_event(
                session,
                run,
                "step_started",
                {"step": step_key, "attempt": step.attempt_count},
            )

        with self._heartbeat_loop(run_id):
            output = handler(run_id)

        with self.session_factory() as session, session.begin():
            run = self._locked_owned_run(session, run_id)
            if self._cancel_if_requested(session, run):
                return False
            step = session.scalar(
                select(StepExecution)
                .where(StepExecution.run_id == run.id, StepExecution.step_key == step_key)
                .with_for_update()
            )
            step.status = "succeeded"
            step.output_summary = self._summary(output)
            step.error_code = None
            step.completed_at = utcnow()
            step.updated_at = step.completed_at
            previous = session.scalar(
                select(Checkpoint)
                .where(Checkpoint.run_id == run.id)
                .order_by(Checkpoint.sequence.desc())
                .limit(1)
            )
            completed = list(previous.state.get("completed_steps", [])) if previous else []
            if step_key not in completed:
                completed.append(step_key)
            sequence = (previous.sequence if previous else 0) + 1
            session.add(
                Checkpoint(
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                    sequence=sequence,
                    state={"completed_steps": completed},
                )
            )
            append_event(session, run, "step_completed", {"step": step_key})
            self._renew(run)
            if final:
                now = utcnow()
                run.status = "succeeded"
                run.result = output
                run.worker_id = None
                run.lease_expires_at = None
                run.heartbeat_at = None
                run.updated_at = now
                run.completed_at = now
                append_event(session, run, "run_succeeded", {"status": "succeeded"})
        return True

    def _validate_input(self, run_id: UUID):
        with self.session_factory() as session:
            run, version = self._context(session, run_id)
            errors = sorted(
                Draft202012Validator(version.snapshot["input_schema"]).iter_errors(run.input),
                key=lambda item: list(item.path),
            )
            if errors:
                raise DeterministicRunError("input_schema_invalid", errors[0].message)
            return {"valid": True}

    def _produce_result(self, run_id: UUID):
        with self.session_factory() as session:
            run, version = self._context(session, run_id)
            return {
                "agent": version.snapshot["name"],
                "version": version.version,
                "input": run.input,
            }

    def _context(self, session: Session, run_id: UUID):
        run = session.scalar(
            select(Run).where(Run.id == run_id, Run.workspace_id == self.workspace_id)
        )
        if run is None:
            raise DeterministicRunError("run_not_found", "Run no longer exists")
        version = session.scalar(
            select(AgentVersion).where(
                AgentVersion.id == run.agent_version_id,
                AgentVersion.workspace_id == self.workspace_id,
            )
        )
        if version is None:
            raise DeterministicRunError("agent_version_not_found", "Agent version no longer exists")
        return run, version

    def _locked_owned_run(self, session: Session, run_id: UUID):
        run = session.scalar(
            select(Run)
            .where(Run.id == run_id, Run.workspace_id == self.workspace_id)
            .with_for_update()
        )
        if (
            run is None
            or run.worker_id != self.worker_id
            or run.status
            not in {
                "running",
                "cancelling",
            }
        ):
            raise LostLeaseError
        return run

    def _renew(self, run: Run):
        now = utcnow()
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=self.settings.worker_lease_seconds)
        run.updated_at = now

    @contextmanager
    def _heartbeat_loop(self, run_id: UUID):
        stopped = Event()
        thread = Thread(
            target=self._heartbeat_until_stopped,
            args=(run_id, stopped),
            name=f"heartbeat-{str(run_id)[:8]}",
            daemon=True,
        )
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=1)

    def _heartbeat_until_stopped(self, run_id: UUID, stopped: Event):
        interval = self.settings.worker_heartbeat_seconds
        while not stopped.wait(interval):
            now = utcnow()
            with self.session_factory() as session, session.begin():
                result = session.execute(
                    update(Run)
                    .where(
                        Run.id == run_aid,
                        Run.workspace_id == self.workspace_id,
                        Run.worker_id == self.worker_id,
                        Run.status.in_(["running", "cancelling"]),
                    )
                    .values(
                        heartbeat_at=now,
                        lease_expires_at=now
                        + timedelta(seconds=self.settings.worker_lease_seconds),
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    return

    @staticmethod
    def _summary(value):
        if isinstance(value, dict):
            return {"keys": sorted(value.keys()), "size": len(value)}
        return {"type": type(value).__name__}

    def _cancel_if_requested(self, session: Session, run: Run) -> bool:
        if run.status != "cancelling" and run.cancel_requested_at is None:
            return False
        now = utcnow()
        run.status = "cancelled"
        run.completed_at = now
        run.updated_at = now
        run.worker_id = None
        run.lease_expires_at = None
        run.heartbeat_at = None
        append_event(session, run, "run_cancelled", {"status": "cancelled"})
        return True

    def _fail(self, run_id: UUID, code: str, message: str, *, retryable: bool):
        with self.session_factory() as session, session.begin():
            run = session.scalar(
                select(Run)
                .where(Run.id == run_id, Run.workspace_id == self.workspace_id)
                .with_for_update()
            )
            if run is None or run.worker_id != self.worker_id:
                return
            if self._cancel_if_requested(session, run):
                return
            run.execution_attempts += 1
            run.worker_id = None
            run.lease_expires_at = None
            run.heartbeat_at = None
            run.updated_at = utcnow()
            should_retry = retryable and run.execution_attempts < self.settings.worker_max_attempts
            if should_retry:
                run.status = "queued"
                append_event(
                    session,
                    run,
                    "run_retry_scheduled",
                    {"attempt": run.execution_attempts, "code": code},
                )
            else:
                run.status = "failed"
                run.error = {"code": code, "message": message}
                run.completed_at = utcnow()
                append_event(
                    session,
                    run,
                    "run_failed",
                    {"code": code, "retryable": retryable},
                )

    def run_forever(self):
        logger.info("worker_started", extra={"worker_id": self.worker_id})
        while True:
            try:
                processed = self.run_once()
            except KeyboardInterrupt:
                break
            except Exception:
                logger.exception("worker_iteration_failed", extra={"worker_id": self.worker_id})
                processed = None
            if processed is None:
                time.sleep(self.settings.worker_poll_interval_seconds)
        logger.info("worker_stopped", extra={"worker_id": self.worker_id})
