from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

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


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelConnectionConfig(StrictModel):
    name: Name
    provider: Literal["openai_compatible"] = "openai_compatible"
    model_name: Name
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)]
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "base_url must be an HTTP(S) origin or path without credentials or query"
            )
        return value.rstrip("/")


class ModelConnectionCreate(ModelConnectionConfig):
    api_key: SecretStr

    @field_validator("api_key")
    @classmethod
    def valid_api_key(cls, value: SecretStr):
        if not value.get_secret_value().strip() or len(value.get_secret_value()) > 8192:
            raise ValueError("api_key must contain between 1 and 8192 characters")
        return value


class ModelConnectionPatch(StrictModel):
    name: Name | None = None
    provider: Literal["openai_compatible"] | None = None
    model_name: Name | None = None
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)] | None = (
        None
    )
    api_key: SecretStr | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_nulls(self):
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self

    @field_validator("api_key")
    @classmethod
    def valid_api_key(cls, value: SecretStr | None):
        if value is not None and (
            not value.get_secret_value().strip() or len(value.get_secret_value()) > 8192
        ):
            raise ValueError("api_key must contain between 1 and 8192 characters")
        return value


class ModelConnectionRead(ModelConnectionConfig):
    id: UUID
    workspace_id: str
    credential_configured: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def attach_utc(cls, value: datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class ModelConnectionPage(BaseModel):
    items: list[ModelConnectionRead]
    total: int
    offset: int
    limit: int


class ConnectionTestResult(BaseModel):
    connection_id: UUID
    success: bool
    code: Literal[
        "ok",
        "credential_not_found",
        "credential_encryption_unavailable",
        "credential_decryption_failed",
        "authentication_failed",
        "provider_unreachable",
        "invalid_response",
        "model_not_found",
    ]
    message: str
    latency_ms: float
    tested_at: datetime
