from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas.agents import (
    AgentCreate,
    AgentPage,
    AgentPatch,
    AgentRead,
    VersionPage,
    VersionRead,
)
from app.services.agents import AgentService

router = APIRouter(prefix="/v1/agents", tags=["Agents"])


def service(request: Request, session: Annotated[Session, Depends(get_session)]):
    return AgentService(session, request.app.state.settings.workspace_id)


Service = Annotated[AgentService, Depends(service)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.post("", response_model=AgentRead, status_code=201)
def create_agent(data: AgentCreate, svc: Service):
    return svc.create(data)


@router.get("", response_model=AgentPage)
def list_agents(svc: Service, offset: Offset = 0, limit: Limit = 20, name: str | None = None):
    return svc.list(offset, limit, name)


@router.get("/{agent_id}", response_model=AgentRead)
def get_agent(agent_id: UUID, svc: Service):
    return svc.serialize(svc.get(agent_id))


@router.patch("/{agent_id}", response_model=AgentRead)
def update_agent(agent_id: UUID, data: AgentPatch, svc: Service):
    return svc.update(agent_id, data)


@router.post("/{agent_id}/versions", response_model=VersionRead, status_code=201)
def publish_agent(agent_id: UUID, svc: Service):
    return svc.publish(agent_id)


@router.get("/{agent_id}/versions", response_model=VersionPage)
def list_versions(agent_id: UUID, svc: Service, offset: Offset = 0, limit: Limit = 20):
    return svc.versions(agent_id, offset, limit)


@router.get("/{agent_id}/versions/{version}", response_model=VersionRead)
def get_version(agent_id: UUID, version: Annotated[int, Path(ge=1)], svc: Service):
    return svc.version(agent_id, version)
