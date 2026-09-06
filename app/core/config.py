from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKBENCH_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres@localhost:5432/ai_workbench"
    workspace_id: str = Field(default="default", min_length=1, max_length=100)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    credential_encryption_key: SecretStr | None = None
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)
    worker_lease_seconds: int = Field(default=30, gt=0)
    worker_heartbeat_seconds: int = Field(default=10, gt=0)
    worker_max_attempts: int = Field(default=3, gt=0)

    @model_validator(mode="after")
    def heartbeat_fits_lease(self):
        if self.worker_heartbeat_seconds >= self.worker_lease_seconds:
            raise ValueError("worker heartbeat interval must be shorter than the lease")
        return self
