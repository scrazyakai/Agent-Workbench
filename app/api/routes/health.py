from fastapi import APIRouter, Request
from sqlalchemy import select

from app.db.models import Agent, AgentVersion, ModelConnection, Tool, ToolVersion

router = APIRouter(tags=["Health"])


@router.get("/health")
def health(request: Request):
    with request.app.state.session_factory() as session:
        session.execute(select(Agent.id).limit(1))
        session.execute(select(AgentVersion.id).limit(1))
        session.execute(select(ModelConnection.id).limit(1))
        session.execute(select(Tool.id).limit(1))
        session.execute(select(ToolVersion.id).limit(1))
    return {"status": "ok", "database": "ok"}
