from __future__ import annotations
from pydantic import BaseModel, Field


class ToolUpsert(BaseModel):
    id: str = Field(min_length=1)
    name: str
    description: str = ""
    risk_level: str = "exec_low"
    executor: str = "docker"
    args_schema: dict = {}
    command: list[str]
    cwd: str | None = None
    timeout_sec: int = 120
    allowed_paths: list[str] = []
    is_enabled: bool = True
