from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas.model_connections import (
    ConnectionTestResult,
    ModelConnectionCreate,
    ModelConnectionPage,
    ModelConnectionPatch,
    ModelConnectionRead,
)
from app.services.model_connections import ModelConnectionService

router = APIRouter(prefix="/v1/model-connections", tags=["Model Connections"])


def service(request: Request, session: Annotated[Session, Depends(get_session)]):
    tester = request.app.state.model_connection_tester
    return ModelConnectionService(
        session,
        request.app.state.settings.workspace_id,
        tester,
        request.app.state.credential_cipher,
    )


Service = Annotated[ModelConnectionService, Depends(service)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.post("", response_model=ModelConnectionRead, status_code=201)
def create_model_connection(data: ModelConnectionCreate, svc: Service):
    return svc.create(data)


@router.get("", response_model=ModelConnectionPage)
def list_model_connections(
    svc: Service,
    offset: Offset = 0,
    limit: Limit = 20,
    name: str | None = None,
    enabled: bool | None = None,
):
    return svc.list(offset, limit, name, enabled)


@router.get("/{connection_id}", response_model=ModelConnectionRead)
def get_model_connection(connection_id: UUID, svc: Service):
    return svc.serialize(svc.get(connection_id))


@router.patch("/{connection_id}", response_model=ModelConnectionRead)
def update_model_connection(connection_id: UUID, data: ModelConnectionPatch, svc: Service):
    return svc.update(connection_id, data)


@router.post("/{connection_id}/test", response_model=ConnectionTestResult)
def test_model_connection(connection_id: UUID, svc: Service):
    return svc.test(connection_id)
