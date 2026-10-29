"""Agents 子包 — Agent 执行体。

- ``butler_agent``：PydanticAI 管家执行引擎（模块级函数集合，chat.send 默认循环）。

从 assistant/ 顶层迁入（原 butler_agent.py 保留 re-export 兼容层）。
"""
from assistant.agents.butler_agent import *  # noqa: F401,F403

__all__ = ["get_butler_agent", "is_tool_allowed", "execute_tool_action",
           "resolve_confirmation", "get_confirmation_status", "BUTLER_SYSTEM_PROMPT"]
