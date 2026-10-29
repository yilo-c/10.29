"""mcp_server/tool_runner.py — 内核调用外部工具的统一通道（契约 §1.1-⑤ / M-tools）

v1 审批边界：SandboxManager 是主进程内存态单例，MCP 为独立进程，
故 risk=high 工具在此结构化拒绝（内核不自我批准）；用户走桌面端路径触发审批。

字段名核对（services/tooling/tool_spec.py + generic_tool_bridge.py，2026-08-15）：
  ToolSpecRegistry(config_path=None) 不依赖 cwd —— config_path=None 时 _load 直接 return，
  无默认文件路径，MCP 进程安全；仅当 data_dir 传入才加载 <data_dir>/tool_specs.json。
  ToolSpec.is_available() / get_action_id(cmd) 存在；DYNAMIC_TOOL_PREFIX="tool."
  GenericToolBridge.execute(action_id, payload) -> ToolResult(success, action_id,
  output, error, duration_seconds, extracted, missing_secrets) 字段与模板一致。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from services.tooling.tool_spec import ToolSpecRegistry
from services.tooling.generic_tool_bridge import GenericToolBridge

logger = logging.getLogger("siyi.mcp_server.tool_runner")


class _McpAudit:
    """MCP 进程审计（§5.4.4 退化方案）：audit_rollback.AuditTrail 绑定主进程
    LocalDataStore，MCP 独立进程不可用，故降级写独立 SQLite 审计表
    （WAL 多进程安全，参照 data/sqlite_store.py 的 PRAGMA）。

    不硬造 AuditTrail 实例；如实记录 source=mcp_tool 的执行轨迹。
    """

    def __init__(self, data_dir: str | None):
        self._db_path = str(Path(data_dir) / "audit_mcp.db") if data_dir else None
        self._lock = threading.Lock()
        if self._db_path:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS mcp_tool_audit ("
                        "  entry_id TEXT PRIMARY KEY,"
                        "  ts REAL NOT NULL,"
                        "  tool_id TEXT,"
                        "  command TEXT,"
                        "  success INTEGER,"
                        "  duration_seconds REAL,"
                        "  source TEXT,"
                        "  detail TEXT)"
                    )
                    conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP 审计表初始化失败（降级为仅日志）: %s", exc)
                self._db_path = None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def record(self, tool_id: str, command: str, success: bool,
               duration_seconds: float, detail: dict[str, Any]) -> None:
        detail = dict(detail)
        detail.update({"tool_id": tool_id, "command": command,
                       "success": success, "duration_seconds": duration_seconds,
                       "source": "mcp_tool"})
        if not self._db_path:
            logger.info("mcp_tool_audit %s", json.dumps(detail, ensure_ascii=False))
            return
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO mcp_tool_audit VALUES (?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex[:16], time.time(), tool_id, command,
                     1 if success else 0, duration_seconds, "mcp_tool",
                     json.dumps(detail, ensure_ascii=False)),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp_tool_audit 写入失败（降级为仅日志）: %s", exc)
            logger.info("mcp_tool_audit %s", json.dumps(detail, ensure_ascii=False))


class ToolRunner:
    def __init__(self, data_dir: str | None = None, working_dir: str | None = None):
        config_path = str(Path(data_dir) / "tool_specs.json") if data_dir else None
        self._registry = ToolSpecRegistry(config_path=config_path)
        # MCP 进程无 workspace 概念：working_dir 缺省用用户主目录（命令在此目录下执行）
        self._bridge = GenericToolBridge(self._registry, working_dir=working_dir or str(Path.home()))
        # §5.4.4 审计（退化方案：独立 SQLite，source=mcp_tool）
        self._audit = _McpAudit(data_dir)

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"tool_id": s.tool_id, "display_name": s.display_name,
             "enabled": s.enabled, "available": s.is_available}
            for s in self._registry.list_specs()
        ]

    def run(self, tool_id: str, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not tool_id:
            return {"success": False, "error": "missing tool_id"}
        spec = self._registry.get(tool_id)
        if spec is None:
            return {"success": False, "error": f"unknown tool: {tool_id}"}
        if not spec.enabled:
            return {"success": False, "error": f"tool disabled: {tool_id}"}
        action_id = spec.get_action_id(command)
        found = self._registry.find_by_action_id(action_id)
        if found is None:
            return {"success": False, "error": f"unknown command: {tool_id}.{command}"}
        _, cmd = found
        if cmd.risk == "high":
            # §5.4.4 审计：拒绝点也记一条（source=mcp_tool，success=False）
            self._audit.record(tool_id, command, False, 0.0,
                               {"reason": "risk_high_refused", "requires_shell_approval": True})
            return {
                "success": False,
                "requires_shell_approval": True,
                "error": "高风险工具需在桌面端审批后执行，内核不可自我批准（契约 I2）",
                "tool_id": tool_id, "command": command,
            }
        try:
            t0 = time.time()
            result = asyncio.run(self._bridge.execute(action_id, params or {}))
            dur = time.time() - t0
        except Exception as exc:  # noqa: BLE001
            logger.exception("siyi_tool_run failed: %s", action_id)
            self._audit.record(tool_id, command, False, 0.0, {"error": str(exc)})
            return {"success": False, "error": str(exc), "tool_id": tool_id}
        # §5.4.4 审计：成功/失败返回点（source=mcp_tool）
        self._audit.record(tool_id, command, result.success, dur,
                           {"action_id": result.action_id, "error": result.error,
                            "missing_secrets": result.missing_secrets or []})
        return {
            "success": result.success,
            "tool_id": tool_id,
            "action_id": result.action_id,
            "output": result.output,
            "error": result.error,
            "duration_seconds": result.duration_seconds,
            "extracted": result.extracted or {},
            "missing_secrets": result.missing_secrets or [],
        }
