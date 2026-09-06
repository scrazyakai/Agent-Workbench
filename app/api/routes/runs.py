import asyncio
import json
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas.runs import RunCreate, RunEventPage, RunPage, RunRead, RunStatus, StepRead
from app.services.runs import TERMINAL_STATUSES, RunService

router = APIRouter(prefix="/v1/runs", tags=["Runs"])


def service(request: Request, session: Annotated[Session, Depends(get_session)]):
    return RunService(session, request.app.state.settings.workspace_id)


Service = Annotated[RunService, Depends(service)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.post("", response_model=RunRead, status_code=201)
def create_run(data: RunCreate, svc: Service):
    return svc.create(data)


@router.get("", response_model=RunPage)
def list_runs(
    svc: Service,
    offset: Offset = 0,
    limit: Limit = 20,
    status: RunStatus | None = None,
    target_id: UUID | None = None,
    thread_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
):
    return svc.list(offset, limit, status, target_id, thread_id)


@router.get("/{run_id}", response_model=RunRead)
def get_run(run_id: UUID, svc: Service):
    return svc.serialize(svc.get(run_id))


@router.get("/{run_id}/events", response_model=RunEventPage)
def get_run_events(
    run_id: UUID,
    svc: Service,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Limit = 100,
):
    return svc.events(run_id, after, limit)


@router.post("/{run_id}/cancel", response_model=RunRead)
def cancel_run(run_id: UUID, svc: Service):
    return svc.cancel(run_id)


@router.get("/{run_id}/steps", response_model=list[StepRead])
def get_steps(run_id: UUID, svc: Service):
    return svc.steps(run_id)


@router.get("/{run_id}/stream")
def stream_events(
    run_id: UUID,
    request: Request,
    svc: Service,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[int | None, Header(ge=0)] = None,
):
    svc.get(run_id)  # Fail with the normal scoped 404 before committing SSE headers.
    svc.session.rollback()  # Release the request dependency's connection before streaming.
    factory = request.app.state.session_factory
    workspace = request.app.state.settings.workspace_id

    def batch(cursor):
        # No transaction or connection is held while waiting for the next event.
        with factory() as session:
            service = RunService(session, workspace)
            status = service.get(run_id).status
            page = service.events(run_id, cursor, 100)
            return status, page

    async def generate():
        cursor = max(after, last_event_id or 0)
        heartbeat = time.monotonic()
        while not await request.is_disconnected():
            status, page = await asyncio.to_thread(batch, cursor)
            for event in page["items"]:
                cursor = event.sequence
                yield f"id: {cursor}\nevent: run_event\ndata: {event.model_dump_json()}\n\n"
            if page["has_more"]:
                continue
            if status in TERMINAL_STATUSES:
                yield f"event: done\ndata: {json.dumps({'status': status})}\n\n"
                return
            if time.monotonic() - heartbeat >= 15:
                yield ": heartbeat\n\n"
                heartbeat = time.monotonic()
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
