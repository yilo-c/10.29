"""最小 MCP stdio 协议服务端（纯标准库，零 SDK 依赖）。

实现思忆集作为 MCP Server 所需的协议子集（MCP 2025-06-18 兼容）：
``initialize`` / ``notifications/*`` / ``ping`` / ``tools/list`` / ``tools/call``。

传输 = newline-delimited JSON-RPC 2.0 over stdio —— 与所有 MCP client
内核（DSH / Claude Code / Codex）互通的通用形状。

无 SDK 是刻意选择：数据边界的稳定性只依赖协议本身（已验证：2026-08-15
V2，DSH 经本协议调用纯标准库服务端成功；见 docs/kernel-layering-design）。
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("siyi.mcp_server.protocol")

PROTOCOL_VERSION = "2025-06-18"


@dataclass(frozen=True)
class ToolSpec:
    """一个 MCP 工具：schema（给内核看）+ handler（在思忆集侧执行）。

    ``level``（M5 工具策略层，向后兼容字段）：RO / RW / DANGER。
    缺省视为 RW（services/tooling/tool_policy.DEFAULT_TOOL_LEVEL），
    既有调用（不传 level）行为不变（allow_all 全部放行）。
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]  # args -> 文本结果（通常为 JSON）
    level: str = "RW"


class McpStdioServer:
    """MCP stdio 服务端：按行读 JSON-RPC，分派到注册工具。"""

    def __init__(self, name: str = "siyi", version: str = "0.1.0", tools: list[ToolSpec] | None = None) -> None:
        self.name = name
        self.version = version
        self._tools: dict[str, ToolSpec] = {t.name: t for t in (tools or [])}

    # ── 协议分派（可单测，无需 stdio）──────────────────────────

    def tool_catalog(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
            for t in self._tools.values()
        ]

    def handle_message(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.name, "version": self.version},
                },
            }

        if method.startswith("notifications/"):
            return None  # 通知无应答

        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": self.tool_catalog()}}

        if method == "tools/call":
            spec = self._tools.get(params.get("name", ""))
            if spec is None:
                return {"jsonrpc": "2.0", "id": msg_id,
                        "error": {"code": -32602, "message": f"unknown tool: {params.get('name')!r}"}}
            try:
                text = spec.handler(params.get("arguments") or {})
                return {"jsonrpc": "2.0", "id": msg_id,
                        "result": {"content": [{"type": "text", "text": text}]}}
            except Exception as exc:  # 工具执行错误回给模型（isError），协议不断连
                logger.warning("tool %s failed: %s", spec.name, exc, exc_info=True)
                return {"jsonrpc": "2.0", "id": msg_id,
                        "result": {"content": [{"type": "text", "text": f"error: {exc}"}], "isError": True}}

        if msg_id is not None:
            return {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return None

    # ── stdio 主循环 ───────────────────────────────────────────

    def serve_stdio(self) -> None:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(msg, dict):
                continue
            response = self.handle_message(msg)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
