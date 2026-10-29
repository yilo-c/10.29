"""chat.send 多路径分发模块。

路径分发（2026-09-02 Flip 回翻后现状）：
1. gateway_path          — AgentGateway 外部内核执行器（灰度：仅 params.kernel
                           强制或显式 chat_route="gateway" 时走；resolve_chat_route
                           缺省返回 "legacy"，见 services/agent_gateway/routing.py）
2. aggregate_path        — 多 provider 聚合（仅显式 aggregation 参数）
3. multi_agent_path      — Planner + Worker 多 Agent（仅显式 mode="multi_agent"）
4. pydantic_agent_path   — ★ 默认：管家（butler）单循环工具流（legacy 路由）
5. simple_stream_path    — 最终保底纯流式（无工具）

历史路径：
- stream_tools_path 已废弃（deprecated，见模块内标注）
- pydantic_agent_path 曾为默认（butler 管家单循环）；2026-08-16 Flip 把默认
  切到 gateway，2026-09 回翻后恢复为默认路径
"""

from .helpers import _is_complex_task, build_messages, _chat_send_aggregate
from .aggregate_path import run_aggregate_path
from .multi_agent_path import run_multi_agent_path
from .pydantic_agent_path import run_pydantic_agent_path
from .simple_stream_path import run_simple_stream_path
from .gateway_path import (
    run_gateway_path,
    run_gateway_path_progressive,
    run_gateway_resume,
    _use_gateway_route,
    _resolve_chat_route,
    _get_gateway,
)

__all__ = [
    "_is_complex_task",
    "build_messages",
    "_chat_send_aggregate",
    "run_aggregate_path",
    "run_multi_agent_path",
    "run_pydantic_agent_path",
    "run_simple_stream_path",
    "run_gateway_path",
    "run_gateway_path_progressive",
    "run_gateway_resume",
    "_use_gateway_route",
    "_resolve_chat_route",
    "_get_gateway",
]
