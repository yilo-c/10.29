"""AbstractAgentDriver — 统一的外部 AI 客户端驱动接口。

所有外部 AI 工具（Claude Code、OpenClaw、Hermes、Codex）
都封装为 AgentDriver，注册到 AgentDirectory 供 Orchestrator 调用。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Protocol


# ── 枚举 ─────────────────────────────────────────────────


class AgentType(str, Enum):
    """AI 客户端类型。"""
    CLAUDE_CODE = "claude_code"
    OPENCLAW = "openclaw"
    HERMES = "hermes"
    CODEX = "codex"
    SIYIJI = "siyiji"
    MCP = "mcp"
    TRAE = "trae"
    CUSTOM = "custom"


class AgentCapability(str, Enum):
    """Agent 能力分类。"""
    CODE_REVIEW = "code_review"
    CODE_GENERATION = "code_generation"
    FILE_OPERATIONS = "file_operations"
    WEB_SEARCH = "web_search"
    LONG_RUNNING_TASK = "long_running_task"
    CONVERSATION = "conversation"
    REASONING = "reasoning"
    TASK_PLANNING = "task_planning"
    SYSTEM_OPERATION = "system_operation"
    DATA_ANALYSIS = "data_analysis"


class AgentStatus(str, Enum):
    """Agent 运行时状态。"""
    UNAVAILABLE = "unavailable"
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"


# ── 数据类 ───────────────────────────────────────────────


@dataclass
class AgentInfo:
    """Agent 注册信息 — 描述一个 AI 客户端的身份和能力。"""
    agent_id: str
    agent_type: AgentType
    display_name: str
    description: str
    capabilities: list[AgentCapability] = field(default_factory=list)
    version: str = ""
    is_available: bool = False
    status: AgentStatus = AgentStatus.UNAVAILABLE
    max_concurrent: int = 1
    cost_rank: int = 5                     # 1=最贵, 10=最便宜
    invocation_count: int = 0
    last_error: str = ""
    last_used_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type.value if isinstance(self.agent_type, AgentType) else self.agent_type,
            "display_name": self.display_name,
            "description": self.description,
            "capabilities": [c.value if isinstance(c, AgentCapability) else c for c in self.capabilities],
            "version": self.version,
            "is_available": self.is_available,
            "status": self.status.value if isinstance(self.status, AgentStatus) else self.status,
            "max_concurrent": self.max_concurrent,
            "cost_rank": self.cost_rank,
            "invocation_count": self.invocation_count,
            "last_error": self.last_error,
            "last_used_at": self.last_used_at,
        }


@dataclass
class AgentInvocation:
    """一次 Agent 调用的完整记录。"""
    invocation_id: str
    agent_id: str
    task: str
    context: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"                # pending / running / completed / failed / cancelled
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    tokens_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "agent_id": self.agent_id,
            "task": self.task[:500],
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "tokens_used": self.tokens_used,
        }


# ── Driver 接口 ──────────────────────────────────────────


class AbstractAgentDriver(Protocol):
    """统一的 Agent 驱动接口。

    所有外部 AI 客户端的适配器必须实现此接口。
    """

    agent_info: AgentInfo

    async def execute(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        *,
        timeout: int = 300,
        progress_callback: Callable[[str], None] | None = None,
    ) -> AgentInvocation:
        """执行一个任务并返回结果。"""
        ...

    def cancel(self) -> bool:
        """取消当前正在执行的任务。"""
        ...

    @property
    def is_available(self) -> bool:
        """当前是否可用（CLI 存在、进程正常等）。"""
        ...


# ── 工具函数 ─────────────────────────────────────────────


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
