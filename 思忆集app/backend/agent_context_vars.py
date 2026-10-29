# -*- coding: utf-8 -*-
"""兼容层：agent 上下文变量已下沉到 ``core/agent_context_vars.py``。

本模块仅 re-export，避免破坏既有 ``backend.agent_context_vars`` 导入。
新代码请直接使用 ``core.agent_context_vars``（见 P0-12 跨层防线）。
"""
from core.agent_context_vars import (  # noqa: F401
    _active_chain_id_var,
    _trace_id_var,
    get_trace_id,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
)

__all__ = [
    "_trace_id_var",
    "_active_chain_id_var",
    "new_trace_id",
    "get_trace_id",
    "set_trace_id",
    "reset_trace_id",
]
