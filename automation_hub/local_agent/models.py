"""local_agent.models — 本地代理的核心数据模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class NodeStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DRAINING = "draining"
    REVOKED = "revoked"


class RiskLevel(str, Enum):
    READ = "read"
    EXEC_LOW = "exec_low"
    EXEC_HIGH = "exec_high"
    WRITE = "write"


class RunClaimStatus(str, Enum):
    CLAIMED = "claimed"
    ALREADY_CLAIMED = "already_claimed"
    NOT_FOUND = "not_found"


class SessionEventType(str, Enum):
    USER_REQUEST = "user_request_received"
    PLAN_GENERATED = "plan_generated"
    PROPOSAL_CREATED = "proposal_created"
    APPROVAL_WAITING = "approval_waiting"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_STREAM = "execution_stream_chunk"
    EXECUTION_FINISHED = "execution_finished"
    EXECUTION_FAILED = "execution_failed"


# ---------------------------------------------------------------------------
# 节点注册
# ---------------------------------------------------------------------------

class NodeRegisterRequest(BaseModel):
    node_name: str = Field(min_length=1, max_length=128)
    hostname: str = Field(min_length=1, max_length=256)
    platform: str = Field(min_length=1, max_length=64)
    agent_version: str = Field(default="0.1.0", max_length=32)
    capabilities: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    public_key: str | None = None


class NodeRegisterResponse(BaseModel):
    node_id: str
    issued_token: str
    heartbeat_interval_sec: int = 30
    policy_bundle_version: str


# ---------------------------------------------------------------------------
# 心跳
# ---------------------------------------------------------------------------

class SystemMetrics(BaseModel):
    cpu_percent: float = 0
    memory_percent: float = 0
    disk_percent: float = 0


class HeartbeatRequest(BaseModel):
    status: NodeStatus = NodeStatus.ONLINE
    agent_version: str = "0.1.0"
    policy_bundle_version: str | None = None
    running_sessions: int = 0
    queue_depth: int = 0
    system: SystemMetrics = Field(default_factory=SystemMetrics)
    recent_errors: list[str] = Field(default_factory=list, max_length=20)


class HeartbeatResponse(BaseModel):
    ack: bool = True
    server_time: str
    policy_update_available: bool = False
    commands: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------

class ApprovalRule(BaseModel):
    risk_levels: list[RiskLevel]
    require_approval: bool = True


class PolicyBundle(BaseModel):
    bundle_version: str
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    approval_rules: list[ApprovalRule] = Field(default_factory=list)
    exec_constraints: dict[str, Any] = Field(default_factory=dict)
    cache_ttl_sec: int = 300


# ---------------------------------------------------------------------------
# 会话事件
# ---------------------------------------------------------------------------

class SessionEvent(BaseModel):
    event_type: SessionEventType
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionEventsRequest(BaseModel):
    events: list[SessionEvent] = Field(min_length=1, max_length=100)


# ---------------------------------------------------------------------------
# 执行授权
# ---------------------------------------------------------------------------

class ExecutionAuthorizationRequest(BaseModel):
    node_id: str
    run_id: str
    approval_id: str | None = None
    tool_id: str
    requested_scope: list[str] = Field(default_factory=lambda: ["tool:execute"])
    expires_in_sec: int = Field(default=300, ge=10, le=3600)


class ExecutionAuthorizationResponse(BaseModel):
    authorization_id: str
    run_id: str
    tool_id: str
    granted_scope: list[str]
    expires_at: str


# ---------------------------------------------------------------------------
# Run claim
# ---------------------------------------------------------------------------

class RunClaimRequest(BaseModel):
    node_id: str


class RunClaimResponse(BaseModel):
    status: RunClaimStatus
    run_id: str


# ---------------------------------------------------------------------------
# Run 事件回传
# ---------------------------------------------------------------------------

class RunEvent(BaseModel):
    event_type: str  # stdout, stderr, progress, adapter_diag
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class RunEventsRequest(BaseModel):
    events: list[RunEvent] = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# 节点管理
# ---------------------------------------------------------------------------

class DrainRequest(BaseModel):
    reason: str = ""


class RevokeRequest(BaseModel):
    reason: str = ""
