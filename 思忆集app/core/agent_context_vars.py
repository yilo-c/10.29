# -*- coding: utf-8 -*-
"""Agent 调用链上下文变量（trace_id / 步骤进度 / chain_id）。

历史说明：本模块原位于 ``backend/agent_context_vars.py``，源文件丢失后
仅残留 .pyc。为消除 ``assistant/*`` 与 ``ai/*`` 对 ``backend.*`` 的反向
依赖（P0-12 跨层防线），重建时下沉到 ``core/`` —— 最底层模块，任何层
均可导入。``backend/agent_context_vars.py`` 保留为兼容 re-export。
"""
from __future__ import annotations

import contextvars
import uuid

# 贯穿单次 chat.send 的 trace_id（agent / ai / tool 调用链日志关联）
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agent_trace_id", default=""
)

# 当前激活的 chain_id（chain 引擎执行期标识）
_active_chain_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "active_chain_id", default=""
)


def new_trace_id() -> str:
    """生成新的 trace_id（uuid hex 截断，日志友好）。"""
    return uuid.uuid4().hex[:16]


def get_trace_id() -> str:
    """读取当前上下文的 trace_id；未设置时返回空串。"""
    return _trace_id_var.get()


def set_trace_id(trace_id: str) -> contextvars.Token:
    """设置 trace_id，返回 Token 供 reset_trace_id 还原。"""
    return _trace_id_var.set(trace_id)


def reset_trace_id(token: contextvars.Token) -> None:
    """还原 trace_id（跨上下文 reset 失败时静默降级）。"""
    try:
        _trace_id_var.reset(token)
    except (ValueError, LookupError):
        pass


__all__ = [
    "_trace_id_var",
    "_active_chain_id_var",
    "new_trace_id",
    "get_trace_id",
    "set_trace_id",
    "reset_trace_id",
]
