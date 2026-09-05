from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas.tools import (
    ToolCreate,
    ToolPage,
    ToolPatch,
    ToolRead,
    ToolTestRequest,
    ToolTestResult,
    ToolVersionPage,
    ToolVersionRead,
)
from app.services.tools import ToolService

router = APIRouter(prefix="/v1/tools", tags=["Tools"])


def service(request: Request, session: Annotated[Session, Depends(get_session)]):
    return ToolService(
        session,
        request.app.state.settings.workspace_id,
        request.app.state.credential_cipher,
        request.app.state.tool_runtime,
    )


Service = Annotated[ToolService, Depends(service)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.post("", response_model=ToolRead, status_code=201)
def create_tool(data: ToolCreate, svc: Service):
    return svc.create(data)


@router.get("", response_model=ToolPage)
def list_tools(
    svc: Service,
    offset: Offset = 0,
    limit: Limit = 20,
    name: str | None = None,
    tool_type: Literal["http", "mcp"] | None = None,
    enabled: bool | None = None,
):
    return svc.list(offset, limit, name, tool_type, enabled)


@router.get("/{tool_id}", response_model=ToolRead)
def get_tool(tool_id: UUID, svc: Service):
    return svc.serialize(svc.get(tool_id))


@router.patch("/{tool_id}", response_model=ToolRead)
def update_tool(tool_id: UUID, data: ToolPatch, svc: Service):
    return svc.update(tool_id, data)


@router.post("/{tool_id}/test", response_model=ToolTestResult)
async def test_tool(tool_id: UUID, data: ToolTestRequest, svc: Service):
    return await svc.test(tool_id, data.arguments)


@router.post("/{tool_id}/discover")
async def discover_mcp_tools(tool_id: UUID, svc: Service):
    return {"tools": await svc.discover_mcp(tool_id)}


@router.post("/{tool_id}/versions", response_model=ToolVersionRead, status_code=201)
def publish_tool(tool_id: UUID, svc: Service):
    return svc.publish(tool_id)


@router.get("/{tool_id}/versions", response_model=ToolVersionPage)
def list_tool_versions(tool_id: UUID, svc: Service, offset: Offset = 0, limit: Limit = 20):
    return svc.versions(tool_id, offset, limit)


@router.get("/{tool_id}/versions/{version}", response_model=ToolVersionRead)
def get_tool_version(tool_id: UUID, version: int, svc: Service):
    return svc.version(tool_id, version)
