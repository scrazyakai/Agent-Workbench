from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ModelSettings(StrictModel):
    connection_id: Reference | None = None
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_output_tokens: int = Field(default=1024, gt=0)
    timeout_seconds: int = Field(default=60, gt=0)


class ToolBinding(StrictModel):
    tool_id: Reference
    version: int = Field(gt=0)


class ExecutionLimits(StrictModel):
    max_steps: int = Field(default=20, gt=0)
    max_tool_calls: int = Field(default=10, ge=0)
    timeout_seconds: int = Field(default=300, gt=0)
    token_budget: int = Field(default=10000, gt=0)


class AgentCreate(StrictModel):
    name: Name
    description: str = ""
    owner: str = ""
    tags: list[Reference] = Field(default_factory=list)
    system_prompt: str = ""
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] | None = None
    # model_config is reserved by Pydantic; expose it only as the public JSON alias.
    model_settings: ModelSettings = Field(default_factory=ModelSettings, alias="model_config")
    tool_bindings: list[ToolBinding] = Field(default_factory=list)
    execution_limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def valid_schema(cls, value):
        if value is not None:
            if value.get("$schema", "https://json-schema.org/draft/2020-12/schema") != (
                "https://json-schema.org/draft/2020-12/schema"
            ):
                raise ValueError("Only JSON Schema Draft 2020-12 is supported")
            try:
                Draft202012Validator.check_schema(value)
            except SchemaError as exc:
                raise ValueError(f"Invalid JSON Schema: {exc.message}") from exc
        return value


class AgentPatch(StrictModel):
    name: Name | None = None
    description: str | None = None
    owner: str | None = None
    tags: list[Reference] | None = None
    system_prompt: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    model_settings: ModelSettings | None = Field(default=None, alias="model_config")
    tool_bindings: list[ToolBinding] | None = None
    execution_limits: ExecutionLimits | None = None

    @model_validator(mode="after")
    def reject_nulls(self):
        for field in self.model_fields_set - {"output_schema"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class Timestamps(BaseModel):
    @field_validator("created_at", "updated_at", "published_at", check_fields=False)
    @classmethod
    def attach_utc(cls, value: datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class AgentRead(AgentCreate, Timestamps):
    id: UUID
    workspace_id: str
    latest_version: int | None
    created_at: datetime
    updated_at: datetime


class VersionRead(Timestamps):
    id: UUID
    agent_id: UUID
    workspace_id: str
    version: int
    snapshot: AgentCreate
    published_at: datetime


class AgentPage(BaseModel):
    items: list[AgentRead]
    total: int
    offset: int
    limit: int


class VersionPage(BaseModel):
    items: list[VersionRead]
    total: int
    offset: int
    limit: int
