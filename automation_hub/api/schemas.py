"""模块说明：Pydantic 数据模型。"""

from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime


class Script(BaseModel):
    name: str
    description: str
    created_at: Optional[datetime] = None


class Parameter(BaseModel):
    name: str
    type: str
    required: bool
    description: str


class ScriptDetail(Script):
    parameters: List[Parameter]


class Task(BaseModel):
    id: Optional[int] = None
    name: str
    script_name: str
    parameters: Optional[str] = None
    status: str = "pending"
    created_at: Optional[datetime] = None
    scheduled_time: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class RunBase(BaseModel):
    run_id: str
    script_name: str
    parameters: Optional[str] = None
    status: str = "queued"
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    log_file_path: Optional[str] = None
    result: Optional[str] = None
    error_msg: Optional[str] = None


class RunCreate(BaseModel):
    script_name: str
    parameters: Optional[Dict[str, Any]] = None


class RunResponse(RunBase):
    pass


class ComponentHealth(BaseModel):
    name: str
    status: str  # "ok" | "degraded" | "down"
    detail: Optional[str] = None
    latency_ms: Optional[float] = None


class HealthCheck(BaseModel):
    status: str  # "ok" | "degraded" | "down"
    timestamp: datetime
    components: Optional[list[ComponentHealth]] = None