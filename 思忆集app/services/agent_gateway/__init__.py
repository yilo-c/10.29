"""AgentGateway —— 执行内核可替换端口（M1）。

分层设计见 ``docs/kernel-layering-design-2026-08-15.md``：
功能路径层只依赖 ``protocol.AgentGateway`` 抽象；具体内核（DSH /
Claude Code / Codex / 未来任意）是 ``adapters`` 里的可替换实现，
经 ``factory`` 按 settings 键 ``agent_kernel.type`` 选择。

已验证（2026-08-15，V3/V5）：同一功能路径代码经本端口先后驱动
DSH 与 Claude Code 成功执行，零代码改动。
"""

from .adapters import ClaudeCodeGateway, CodexGateway, DshGateway, NoneGateway
from .auto import DEFAULT_CANDIDATE_ORDER, AutoGateway
from .factory import (
    AGENT_GATEWAY_ADAPTERS,
    DEFAULT_AGENT_KERNEL_CONFIG,
    SETTINGS_KEY,
    build_gateway,
    build_gateway_from_repository,
    build_gateway_from_settings_value,
    parse_agent_kernel_value,
)
from .health import GatewayHealthMonitor
from .protocol import AgentGateway, AgentRun, AgentRunEvent, BaseGateway

__all__ = [
    "AgentGateway",
    "AgentRun",
    "AgentRunEvent",
    "BaseGateway",
    "DshGateway",
    "ClaudeCodeGateway",
    "CodexGateway",
    "NoneGateway",
    "AutoGateway",
    "DEFAULT_CANDIDATE_ORDER",
    "GatewayHealthMonitor",
    "AGENT_GATEWAY_ADAPTERS",
    "DEFAULT_AGENT_KERNEL_CONFIG",
    "SETTINGS_KEY",
    "build_gateway",
    "build_gateway_from_repository",
    "build_gateway_from_settings_value",
    "parse_agent_kernel_value",
]
