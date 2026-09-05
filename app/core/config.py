from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKBENCH_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres@localhost:5432/ai_workbench"
    workspace_id: str = Field(default="default", min_length=1, max_length=100)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
