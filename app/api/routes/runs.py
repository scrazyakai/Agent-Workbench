from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas.runs import RunCreate, RunEventPage, RunPage, RunRead, RunStatus
from app.services.runs import RunService

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
