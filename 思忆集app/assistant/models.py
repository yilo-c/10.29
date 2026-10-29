"""assistant_autonomy 的共享数据模型。

包含 AgentTask、PlannedStep、IntentDepth 三级分类、ChainNode DAG 节点。
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


@dataclass
class PlannedStep:
    step_id: str
    action_id: str
    payload: dict[str, Any]
    description: str = ""
    risk_level: str = "low"
    timeout_seconds: int = 20
    retries: int = 1


@dataclass
class StepEvent:
    """执行步骤的实时事件——由 ExecutionLayer 在每步前后广播。"""
    type: str     # step_start | step_end | step_blocked | step_error | need_approval
    step_index: int
    action_id: str
    description: str = ""
    status: str = ""              # ok | failed | timeout | blocked | degraded_readonly
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTask:
    goal: str
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: str = "assistant.goal"
    status: str = "pending"
    delivery_channel: str = "chat"
    chat_session_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    profile_snapshot: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    # Checkpoint 相关字段
    checkpoint_id: str | None = None        # 最新 checkpoint ID
    checkpoint_version: int = 0              # 最新 checkpoint 版本号
    completed_step_indices: list[int] = field(default_factory=list)  # 已完成的步骤索引
    transition_history: list[dict[str, Any]] = field(default_factory=list)  # 状态转换历史
    # 执行计划相关
    plan_id: str | None = None               # 关联的执行计划 ID
    # 事务相关
    is_transactional: bool = False           # 是否为事务性任务
    file_snapshots: dict[str, Any] = field(default_factory=dict)  # 文件快照

    def set_steps(self, steps: list[PlannedStep]) -> None:
        self.steps = [
            {
                "step_id": step.step_id,
                "action_id": step.action_id,
                "risk_level": step.risk_level,
                "payload": dict(step.payload),
                "timeout_seconds": step.timeout_seconds,
                "retries": step.retries,
            }
            for step in steps
        ]
        self.touch()

    def set_status(self, status: str, *, result: dict[str, Any] | None = None) -> None:
        self.status = str(status or "pending")
        if result is not None:
            self.result = dict(result)
        self.touch()

    def mark_step_completed(self, step_index: int) -> None:
        """标记一个步骤为已完成。"""
        if step_index not in self.completed_step_indices:
            self.completed_step_indices.append(step_index)
        self.touch()

    def is_step_completed(self, step_index: int) -> bool:
        return step_index in self.completed_step_indices

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentTask":
        """从字典重建 AgentTask，兼容旧数据。"""
        task = cls(
            goal=str(data.get("goal") or ""),
            task_id=str(data.get("task_id") or uuid.uuid4().hex[:12]),
            source=str(data.get("source") or "assistant.goal"),
            status=str(data.get("status") or "pending"),
            delivery_channel=str(data.get("delivery_channel") or "chat"),
            chat_session_id=str(data.get("chat_session_id") or ""),
            created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
            updated_at=data.get("updated_at"),
            steps=list(data.get("steps") or []),
            profile_snapshot=dict(data.get("profile_snapshot") or {}),
            result=dict(data.get("result") or {}),
            checkpoint_id=data.get("checkpoint_id"),
            checkpoint_version=int(data.get("checkpoint_version") or 0),
            completed_step_indices=list(data.get("completed_step_indices") or []),
            transition_history=list(data.get("transition_history") or []),
            plan_id=data.get("plan_id"),
            is_transactional=bool(data.get("is_transactional") or False),
            file_snapshots=dict(data.get("file_snapshots") or {}),
        )
        return task


# ═══════════════════════════════════════════════════════════════
# MCP-13: Judge 意图深度三级分类
# ═══════════════════════════════════════════════════════════════

class IntentDepth(str, Enum):
    """意图深度三级分类。

    L1 直接回答：简单问答，不需要额外上下文或工具
    L2 单步工具调用：需要一次工具调用（记忆/文件/网络）
    L3 多步 Agent 链：复杂任务需要多步执行、条件分支
    """
    L1_DIRECT_ANSWER = "L1"
    L2_SINGLE_TOOL = "L2"
    L3_AGENT_CHAIN = "L3"

    @classmethod
    def label(cls, depth: IntentDepth) -> str:
        labels = {
            cls.L1_DIRECT_ANSWER: "直接回答",
            cls.L2_SINGLE_TOOL: "单步工具调用",
            cls.L3_AGENT_CHAIN: "多步 Agent 链",
        }
        return labels.get(depth, "未知")

    @classmethod
    def cost_hint(cls, depth: IntentDepth) -> str:
        hints = {
            cls.L1_DIRECT_ANSWER: "低成本 ~0.001垄",
            cls.L2_SINGLE_TOOL: "中成本 ~0.01垄",
            cls.L3_AGENT_CHAIN: "高成本 ~0.1垄+",
        }
        return hints.get(depth, "未知")


# ═══════════════════════════════════════════════════════════════
# MCP-13: Agent 链 DAG 节点
# ═══════════════════════════════════════════════════════════════

@dataclass
class ChainNode:
    """Agent 链中的一个节点（DAG 图节点）。

    支持串行/并行/条件分支，包含委派目标和自我反思结果。
    """
    node_id: str
    description: str
    action_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    # DAG 流程控制
    depends_on: list[str] = field(default_factory=list)   # 前置节点 ID 列表
    mode: str = "serial"                                   # serial | parallel | conditional
    condition: str = ""                                    # 条件分支表达式
    # 委派目标（Task 5）
    delegate_to: str = "local"                             # local | cloud | mcp | automation
    # 风险
    risk_level: str = "low"                                # low | medium | high
    # 重试
    max_retries: int = 2
    # 执行结果
    status: str = "pending"                                # pending | running | completed | failed | skipped
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    retry_count: int = 0
    # 反思结果（Task 3）
    reflection: dict[str, Any] = field(default_factory=dict)
    # 时间
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentChainPlan:
    """Agent 链执行计划——展示给用户确认。"""
    goal: str
    nodes: list[ChainNode] = field(default_factory=list)
    total_steps: int = 0
    estimated_seconds: int = 0
    highest_risk: str = "low"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def serial_count(self) -> int:
        return sum(1 for n in self.nodes if n.mode == "serial")

    @property
    def parallel_groups(self) -> int:
        """估算并行组数量（简单按依赖拓扑层级算）。"""
        if not self.nodes:
            return 0
        # 按拓扑层级分组
        remaining = set(n.node_id for n in self.nodes)
        level = 0
        while remaining:
            # 找出所有依赖已完成的节点
            ready = {
                nid for nid in remaining
                if not any(dep in remaining for dep in self._node_map.get(nid, {}).get("depends_on", []))
            }
            if not ready:
                break
            remaining -= ready
            level += 1
        return level

    @property
    def _node_map(self) -> dict[str, dict[str, Any]]:
        return {n.node_id: {"depends_on": n.depends_on} for n in self.nodes}

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "total_steps": self.total_steps,
            "estimated_seconds": self.estimated_seconds,
            "highest_risk": self.highest_risk,
            "serial_count": self.serial_count,
            "parallel_groups": self.parallel_groups,
            "nodes": [n.to_dict() for n in self.nodes],
            "created_at": self.created_at,
        }

    def summary_text(self) -> str:
        """返回人类可读的执行计划摘要。"""
        parts = [f"馃搵 执行计划：{self.goal}"]
        parts.append(f"共 {self.total_steps} 步，预计 {self.estimated_seconds} 秒")
        parts.append(f"最高风险：{self.highest_risk}")
        for n in self.nodes:
            marker = "馃攧" if n.mode == "parallel" else "➡️" if n.mode == "serial" else "馃攢"
            risk_tag = "⚠️ " if n.risk_level == "high" else "ℹ️ "
            parts.append(f"  {marker} {risk_tag}{n.description} ({n.delegate_to})")
        return "\n".join(parts)
