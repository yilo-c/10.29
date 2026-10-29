"""MCP capability mixin for AssistantCapabilityRouter."""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any, Protocol
from services.capability_types import CapabilitySpec

logger = logging.getLogger("siyi.assistant.autonomy.router.mcp")

# ── 七维-安C3(a)：MCP 工具风险/确认映射（risk 不再恒 medium）──────────
# 映射优先级：server 配置显式 risk > 工具注解 > 名称破坏性关键词 > 保守 medium。
_DESTRUCTIVE_NAME_HINTS = (
    "delete",
    "remove",
    "destroy",
    "purge",
    "drop",
    "kill",
    "terminate",
    "uninstall",
    "wipe",
    "erase",
)
# server 配置 risk 取值别名（low/medium/high 与 RiskLevel R1-R4 词汇均接受）
_SERVER_RISK_ALIASES = {
    "low": "low",
    "read": "low",
    "medium": "medium",
    "write": "medium",
    "modify": "medium",
    "high": "high",
    "destructive": "high",
}
_RISK_TO_CONFIRMATION = {"low": "auto", "medium": "first_time", "high": "every_time"}


def map_mcp_tool_risk(
    tool: dict[str, Any],
    server_config: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """把 MCP 工具元数据映射为 (risk_level, confirmation_level)。

    七维-安C3(a)：risk 不再恒 medium、confirmation 不再恒 first_time。

    优先级：
    1. server 配置显式 risk（接受 low/medium/high 与 read/write/modify/
       destructive 别名；缺省 destructive→high；非法值忽略）；
    2. 工具注解：readOnlyHint=True → low；destructiveHint=True → high；
    3. 工具名破坏性关键词（delete/remove/... 等）→ high；
    4. 其余（未知/读写类）→ 保守 medium。

    confirmation 相应映射：low→auto、medium→first_time、high→every_time。
    """
    annotations = tool.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {}
    name = str(tool.get("name") or "").strip().lower()

    risk = ""
    if isinstance(server_config, dict):
        alias = _SERVER_RISK_ALIASES.get(str(server_config.get("risk") or "").strip().lower())
        if alias:
            risk = alias
    if not risk:
        if annotations.get("readOnlyHint") is True:
            risk = "low"
        elif annotations.get("destructiveHint") is True:
            risk = "high"
        elif any(hint in name for hint in _DESTRUCTIVE_NAME_HINTS):
            risk = "high"
        else:
            risk = "medium"
    return risk, _RISK_TO_CONFIRMATION[risk]


class MCPRouterHost(Protocol):
    """MCPRouterMixin 依赖的宿主契约 — AssistantCapabilityRouter 由 __init__ 注入。"""

    capability_registry: Any


class MCPRouterMixin:
    """MCP 能力混入 — 管理 MCP 工具列表、调用、注册。"""

    if TYPE_CHECKING:
        capability_registry: Any

    def _get_mcp_manager(self):
        # 线 B（2026-08-31）：自研 mcp/ 客户端退役归档，壳内不再懒构建第三方客户端。
        # 只保留宿主注入通道（capability_router(mcp_manager=...)）；未注入时恒为 None，
        # 下游 _mcp_tools_list / _mcp_tool_call 按既有分支返回 mcp_unavailable。
        return self.mcp_manager

    def _get_subagent_manager(self):
        return self.knowledge_domain.get_subagent_manager()

    def _mcp_tools_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        manager = self._get_mcp_manager()
        if manager is None:
            return {"success": False, "reason": "mcp_unavailable"}
        tools = manager.list_tools(refresh=bool(payload.get("refresh", False)))
        self._register_mcp_capabilities(tools)
        return {"success": True, "count": len(tools), "tools": tools}

    def _mcp_tool_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        manager = self._get_mcp_manager()
        if manager is None:
            return {"success": False, "reason": "mcp_unavailable"}
        server = str(payload.get("server") or "").strip()
        tool = str(payload.get("tool") or "").strip()
        if not server or not tool:
            return {"success": False, "reason": "missing_server_or_tool"}
        result = manager.call_tool(server, tool, arguments=dict(payload.get("arguments") or {}))
        return {"success": True, "server": server, "tool": tool, "result": result}

    def _register_mcp_capabilities(self, tools: list[dict[str, Any]]) -> None:
        """Register MCP tools into CapabilityRegistry so Planner can discover them."""
        registry = self.capability_registry
        if registry is None:
            return
        for tool in tools:
            action_id = tool.get("action_id", "")
            if not action_id:
                continue
            if registry.get_capability(action_id) is not None:
                continue
            risk, confirmation = map_mcp_tool_risk(tool)
            spec = CapabilitySpec(
                capability_id=action_id,
                display_name=tool.get("name", action_id),
                description=tool.get("description", ""),
                category="mcp",
                group="mcp",
                source="mcp",
                enabled=True,
                risk_level=risk,
                confirmation_level=confirmation,
                payload_schema=dict(tool.get("inputSchema") or {}),
            )
            registry.register_capability(spec)
            logger.debug("registered MCP capability: %s (risk=%s, confirmation=%s)", action_id, risk, confirmation)
