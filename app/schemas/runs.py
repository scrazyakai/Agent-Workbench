from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

RunStatus = Literal[
    "queued", "running", "succeeded", "failed", "timed_out", "cancelling", "cancelled"
]
OptionalReference = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunTarget(StrictModel):
    type: Literal["agent"] = "agent"
    id: UUID
    version: int = Field(ge=1)


class RunCreate(StrictModel):
    execution_mode: Literal["model", "deterministic"] = "model"
    target: RunTarget
    thread_id: OptionalReference | None = None
    input: dict[str, Any]
    idempotency_key: OptionalReference | None = None


class RunSummary(BaseModel):
    execution_mode: Literal["model", "deterministic"]
    usage: dict[str, Any]
    id: UUID
    workspace_id: str
    target: RunTarget
    thread_id: str | None
    status: RunStatus
    execution_attempts: int
    recovery_count: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @field_validator("created_at", "updated_at", "started_at", "completed_at")
    @classmethod
    def attach_utc(cls, value: datetime | None):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class RunRead(RunSummary):
    input: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    cancel_requested_at: datetime | None

    @field_validator("cancel_requested_at")
    @classmethod
    def attach_cancel_utc(cls, value: datetime | None):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class RunPage(BaseModel):
    items: list[RunSummary]
    total: int
    offset: int
    limit: int


class RunEventRead(BaseModel):
    id: UUID
    run_id: UUID
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def attach_utc(cls, value: datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class RunEventPage(BaseModel):
    items: list[RunEventRead]
    next_cursor: int
    has_more: bool


class StepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    step_key: str
    status: str
    attempt_count: int
    input_summary: dict | None
    output_summary: dict | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
