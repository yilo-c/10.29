"""统一任务领域契约（P1-2 第 0 步）。

思忆集中"任务"散落在五个模型（task_queue task / cron job / assistant_task /
sandbox 审批 / chain 节点），本契约只定义统一投影口径，**不做强制迁移**：
各执行路径按此口径向 TraceRecorder 上报，观测层即可跨类型统一分析。

契约字段（task_runs）：
- task_id:   全局唯一（建议前缀区分来源：chat-*/cron-*/agent-*/queue-*）
- task_type: TASK_TYPE_* 之一
- status:    TASK_STATUS_* 状态机（running → completed/failed/interrupted）
- source:    产生方（butler / cron / task_queue / chain）
"""
from __future__ import annotations

# ── 任务类型 ──
TASK_TYPE_CHAT_TOOL = "chat_tool"      # 对话中的工具任务（butler）
TASK_TYPE_CRON = "cron"                # 定时任务单次执行
TASK_TYPE_AGENT_TASK = "agent_task"    # 助手编排任务（checkpoint 可恢复）
TASK_TYPE_QUEUE = "queue"              # 持久队列任务

TASK_TYPES = frozenset({
    TASK_TYPE_CHAT_TOOL,
    TASK_TYPE_CRON,
    TASK_TYPE_AGENT_TASK,
    TASK_TYPE_QUEUE,
})

# ── 任务状态机 ──
TASK_STATUS_RUNNING = "running"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_INTERRUPTED = "interrupted"  # 崩溃/中断，可由 checkpoint 续跑

TASK_STATUSES = frozenset({
    TASK_STATUS_RUNNING,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_INTERRUPTED,
})

# ── 步骤类型 ──
STEP_TYPE_TOOL_CALL = "tool_call"
STEP_TYPE_TOOL_RESULT = "tool_result"
STEP_TYPE_STEP = "step"          # agent 推理步骤
STEP_TYPE_DECISION = "decision"  # 规划/分流决策

__all__ = [
    "TASK_TYPE_CHAT_TOOL", "TASK_TYPE_CRON", "TASK_TYPE_AGENT_TASK", "TASK_TYPE_QUEUE",
    "TASK_TYPES",
    "TASK_STATUS_RUNNING", "TASK_STATUS_COMPLETED", "TASK_STATUS_FAILED",
    "TASK_STATUS_INTERRUPTED", "TASK_STATUSES",
    "STEP_TYPE_TOOL_CALL", "STEP_TYPE_TOOL_RESULT", "STEP_TYPE_STEP", "STEP_TYPE_DECISION",
]
