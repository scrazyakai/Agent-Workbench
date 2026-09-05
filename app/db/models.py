from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="uq_agent_workspace_name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Complete validated draft configuration; API serialization is handled in the service.
    config: Mapped[dict] = mapped_column(JSON)
    latest_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version", name="uq_agent_version"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("agents.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
