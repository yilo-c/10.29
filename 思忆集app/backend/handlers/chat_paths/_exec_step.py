"""chat_paths 执行步骤上报模块（P0 修复，2026-08-31）。

此前 pydantic_agent_path.py / smolagents_path.py 多处 import 本模块但文件
缺失（git 历史亦无），导致 chat 管家(smolagents)/pydantic agent 路径在运行
到对应函数时必然 ImportError。本模块补齐两个约定函数，按
assistant/trace_recorder.py 的 exec.step 事件 payload 约定经 EventBus 上报
（TraceRecorder 订阅后写 SQLite，供 trace.* RPC 观测）。

- make_task_id(session_id, prefix="chat")：生成一次 chat 运行观测的任务 id。
- emit_exec_step(payload)：同步、尽力而为地上报一条 exec.step 事件；
  EventBus 不可用时静默降级，绝不抛错打断 chat 流（调用点多在流式循环内）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("siyi.chat_paths.exec_step")


def make_task_id(session_id: str, prefix: str = "chat") -> str:
    """生成执行观测任务 id（每次调用唯一，可跨会话回溯到 task_id）。"""
    del session_id  # 保留签名兼容：session_id 仅作可读性，id 唯一性用 uuid 保证
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def emit_exec_step(payload: dict[str, Any] | None = None) -> None:
    """上报一条 exec.step 事件（TraceRecorder 订阅写 SQLite）。

    payload 约定见 trace_recorder.py:47——{task_id?, task_type?, title?,
    source?, step_type, tool?, summary?, status?}。
    """
    if not payload:
        return
    try:
        from core.events import AppEvent, event_bus

        event_bus.emit(AppEvent("exec.step", payload=dict(payload), source="chat_paths"))
    except Exception:  # noqa: BLE001 —— 尽力而为，绝不打断 chat 流
        logger.debug("emit exec.step failed", exc_info=True)
