from copy import deepcopy
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.db.models import Agent, AgentVersion, ModelConnection, Tool, ToolVersion, utcnow
from app.schemas.agents import AgentCreate, AgentPatch, AgentRead, VersionRead


class AgentService:
    def __init__(self, session: Session, workspace_id: str):
        self.session = session
        self.workspace_id = workspace_id

    def get(self, agent_id: UUID, *, lock=False):
        query = select(Agent).where(Agent.id == agent_id, Agent.workspace_id == self.workspace_id)
        if lock:
            query = query.with_for_update()
        agent = self.session.scalar(query)
        if agent is None:
            raise DomainError(404, "agent_not_found", "Agent not found")
        return agent

    @staticmethod
    def serialize(agent):
        return AgentRead(
            **agent.config,
            id=agent.id,
            workspace_id=agent.workspace_id,
            latest_version=agent.latest_version,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )

    @staticmethod
    def serialize_version(version):
        return VersionRead.model_validate(version, from_attributes=True)

    def commit(self):
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DomainError(409, "conflict", "Agent name or version already exists") from exc

    def create(self, data: AgentCreate):
        agent = Agent(
            workspace_id=self.workspace_id,
            name=data.name,
            config=data.model_dump(mode="json", by_alias=True),
        )
        self.session.add(agent)
        self.commit()
        return self.serialize(agent)

    def update(self, agent_id: UUID, data: AgentPatch):
        agent = self.get(agent_id, lock=True)
        config = deepcopy(agent.config)
        config.update(data.model_dump(mode="json", by_alias=True, exclude_unset=True))
        try:
            validated = AgentCreate.model_validate(config)
        except ValidationError as exc:
            raise DomainError(
                422,
                "validation_error",
                "Invalid agent configuration",
                exc.errors(include_context=False, include_input=False),
            ) from exc
        agent.config = validated.model_dump(mode="json", by_alias=True)
        agent.name = validated.name
        agent.updated_at = utcnow()
        self.commit()
        return self.serialize(agent)

    def list(self, offset, limit, name=None, tag=None):
        filters = [Agent.workspace_id == self.workspace_id]
        if name is not None:
            filters.append(Agent.name.contains(name, autoescape=True))
        if tag is not None:
            filters.append(cast(Agent.config["tags"], JSONB).contains([tag]))
        total = self.session.scalar(select(func.count()).select_from(Agent).where(*filters))
        rows = self.session.scalars(
            select(Agent)
            .where(*filters)
            .order_by(Agent.created_at, Agent.id)
            .offset(offset)
            .limit(limit)
        )
        return {
            "items": [self.serialize(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def publish(self, agent_id: UUID):
        # The same row lock protects draft edits and version allocation on PostgreSQL.
        agent = self.get(agent_id, lock=True)
        details = []
        if not agent.config["system_prompt"].strip():
            details.append({"field": "system_prompt", "message": "System prompt is required"})
        if not agent.config["model_config"]["connection_id"]:
            details.append(
                {
                    "field": "model_config.connection_id",
                    "message": "Model connection reference is required",
                }
            )
        else:
            try:
                connection_id = UUID(agent.config["model_config"]["connection_id"])
            except (TypeError, ValueError):
                connection_id = None
            connection = None
            if connection_id is not None:
                connection = self.session.scalar(
                    select(ModelConnection)
                    .where(
                        ModelConnection.id == connection_id,
                        ModelConnection.workspace_id == self.workspace_id,
                    )
                    .with_for_update()
                )
            if connection is None:
                details.append(
                    {
                        "field": "model_config.connection_id",
                        "message": "Model connection does not exist",
                    }
                )
            elif not connection.enabled:
                details.append(
                    {
                        "field": "model_config.connection_id",
                        "message": "Model connection is disabled",
                    }
                )
        for index, binding in enumerate(agent.config["tool_bindings"]):
            field = f"tool_bindings.{index}"
            try:
                tool_id = UUID(binding["tool_id"])
            except (KeyError, TypeError, ValueError):
                tool_id = None
            tool = None
            version = None
            if tool_id is not None:
                tool = self.session.scalar(
                    select(Tool).where(
                        Tool.id == tool_id,
                        Tool.workspace_id == self.workspace_id,
                    )
                )
                version = self.session.scalar(
                    select(ToolVersion).where(
                        ToolVersion.tool_id == tool_id,
                        ToolVersion.workspace_id == self.workspace_id,
                        ToolVersion.version == binding["version"],
                    )
                )
            if tool is None:
                details.append({"field": field, "message": "Tool does not exist"})
            elif not tool.enabled:
                details.append({"field": field, "message": "Tool is disabled"})
            elif version is None:
                details.append({"field": field, "message": "Tool version does not exist"})
        if details:
            raise DomainError(422, "publish_validation_failed", "Agent is not ready", details)
        version = AgentVersion(
            agent_id=agent.id,
            workspace_id=self.workspace_id,
            version=(agent.latest_version or 0) + 1,
            snapshot=deepcopy(agent.config),
        )
        self.session.add(version)
        agent.latest_version = version.version
        agent.updated_at = utcnow()
        self.commit()
        return self.serialize_version(version)

    def versions(self, agent_id: UUID, offset, limit):
        self.get(agent_id)
        filters = [
            AgentVersion.agent_id == agent_id,
            AgentVersion.workspace_id == self.workspace_id,
        ]
        total = self.session.scalar(select(func.count()).select_from(AgentVersion).where(*filters))
        rows = self.session.scalars(
            select(AgentVersion)
            .where(*filters)
            .order_by(AgentVersion.version.desc())
            .offset(offset)
            .limit(limit)
        )
        return {
            "items": [self.serialize_version(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def version(self, agent_id: UUID, number: int):
        self.get(agent_id)
        row = self.session.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_id == agent_id,
                AgentVersion.workspace_id == self.workspace_id,
                AgentVersion.version == number,
            )
        )
        if row is None:
            raise DomainError(404, "version_not_found", "Version not found")
        return self.serialize_version(row)
