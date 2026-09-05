from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(max_length=4000)]
InputReference = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolAuth(StrictModel):
    type: Literal["none", "bearer", "header"] = "none"
    header_name: str = "Authorization"

    @model_validator(mode="after")
    def validate_header(self):
        if self.type == "bearer":
            self.header_name = "Authorization"
        elif self.type == "header":
            if not self.header_name.strip() or len(self.header_name) > 200:
                raise ValueError("header_name must contain between 1 and 200 characters")
            if self.header_name.lower() in {"host", "content-length", "transfer-encoding"}:
                raise ValueError("header_name is controlled by the HTTP client")
        return self


class HttpRetryPolicy(StrictModel):
    max_attempts: int = Field(default=1, ge=1, le=3)
    backoff_seconds: float = Field(default=0.25, ge=0, le=5)
    retry_statuses: list[int] = Field(default_factory=lambda: [502, 503, 504])

    @field_validator("retry_statuses")
    @classmethod
    def valid_statuses(cls, values: list[int]):
        if any(value < 500 or value > 599 for value in values):
            raise ValueError("retry_statuses may contain only 5xx status codes")
        return list(dict.fromkeys(values))


def validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("endpoint must be an HTTP(S) URL without credentials, query or fragment")
    return value


class HttpToolConfig(StrictModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    endpoint: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)]
    allowed_hosts: list[Name]
    path_params: dict[Name, InputReference] = Field(default_factory=dict)
    query_params: dict[Name, InputReference] = Field(default_factory=dict)
    header_params: dict[Name, InputReference] = Field(default_factory=dict)
    body_mode: Literal["none", "json"] = "json"
    auth: ToolAuth = Field(default_factory=ToolAuth)
    timeout_seconds: int = Field(default=15, ge=1, le=120)
    response_max_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    retry: HttpRetryPolicy = Field(default_factory=HttpRetryPolicy)

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value: str):
        return validate_endpoint(value)

    @model_validator(mode="after")
    def endpoint_must_be_allowed(self):
        hostname = urlsplit(self.endpoint).hostname
        if hostname and hostname.lower() not in {host.lower() for host in self.allowed_hosts}:
            raise ValueError("endpoint hostname must be present in allowed_hosts")
        if self.method in {"GET", "DELETE"} and self.body_mode != "none":
            raise ValueError(f"{self.method} tools must use body_mode=none")
        if self.method != "GET" and self.retry.max_attempts > 1:
            raise ValueError("automatic retries are allowed only for GET tools")
        return self


class McpToolConfig(StrictModel):
    transport: Literal["streamable_http"] = "streamable_http"
    server_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)]
    remote_tool_name: Name
    allowed_hosts: list[Name]
    auth: ToolAuth = Field(default_factory=ToolAuth)
    timeout_seconds: int = Field(default=30, ge=1, le=300)

    @field_validator("server_url")
    @classmethod
    def valid_server_url(cls, value: str):
        return validate_endpoint(value)

    @model_validator(mode="after")
    def endpoint_must_be_allowed(self):
        hostname = urlsplit(self.server_url).hostname
        if hostname and hostname.lower() not in {host.lower() for host in self.allowed_hosts}:
            raise ValueError("server hostname must be present in allowed_hosts")
        return self


ToolConfig = HttpToolConfig | McpToolConfig


class ToolDefinition(StrictModel):
    name: Name
    description: Description = ""
    owner: Name = "unassigned"
    tags: list[Name] = Field(default_factory=list)
    tool_type: Literal["http", "mcp"]
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] | None = None
    config: ToolConfig
    risk_level: Literal["read", "write", "high"] = "read"
    requires_approval: bool = False
    enabled: bool = True

    @field_validator("input_schema", "output_schema")
    @classmethod
    def valid_schema(cls, value):
        if value is not None:
            try:
                Draft202012Validator.check_schema(value)
            except SchemaError as exc:
                raise ValueError(f"Invalid JSON Schema: {exc.message}") from exc
        return value

    @model_validator(mode="after")
    def config_matches_type(self):
        expected = HttpToolConfig if self.tool_type == "http" else McpToolConfig
        if not isinstance(self.config, expected):
            raise ValueError(f"config does not match tool_type={self.tool_type}")
        if self.risk_level == "high" and not self.requires_approval:
            raise ValueError("high risk tools must require approval")
        return self


class ToolCreate(ToolDefinition):
    credential: SecretStr | None = None

    @field_validator("credential")
    @classmethod
    def valid_credential(cls, value: SecretStr | None):
        if value is not None and (
            not value.get_secret_value().strip() or len(value.get_secret_value()) > 8192
        ):
            raise ValueError("credential must contain between 1 and 8192 characters")
        return value


class ToolPatch(StrictModel):
    name: Name | None = None
    description: Description | None = None
    owner: Name | None = None
    tags: list[Name] | None = None
    tool_type: Literal["http", "mcp"] | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    config: ToolConfig | None = None
    risk_level: Literal["read", "write", "high"] | None = None
    requires_approval: bool | None = None
    enabled: bool | None = None
    credential: SecretStr | None = None

    @model_validator(mode="after")
    def reject_nulls(self):
        for field in self.model_fields_set - {"output_schema"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self

    @field_validator("credential")
    @classmethod
    def valid_credential(cls, value: SecretStr | None):
        if value is not None and (
            not value.get_secret_value().strip() or len(value.get_secret_value()) > 8192
        ):
            raise ValueError("credential must contain between 1 and 8192 characters")
        return value


class ToolRead(ToolDefinition):
    id: UUID
    workspace_id: str
    credential_configured: bool
    latest_version: int | None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def attach_utc(cls, value: datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class ToolPage(BaseModel):
    items: list[ToolRead]
    total: int
    offset: int
    limit: int


class ToolVersionRead(BaseModel):
    id: UUID
    tool_id: UUID
    workspace_id: str
    version: int
    snapshot: ToolDefinition
    published_at: datetime

    @field_validator("published_at")
    @classmethod
    def attach_utc(cls, value: datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class ToolVersionPage(BaseModel):
    items: list[ToolVersionRead]
    total: int
    offset: int
    limit: int


class ToolTestRequest(StrictModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolTestResult(BaseModel):
    tool_id: UUID
    success: bool
    code: str
    message: str
    output: Any | None = None
    duration_ms: float
    tested_at: datetime
