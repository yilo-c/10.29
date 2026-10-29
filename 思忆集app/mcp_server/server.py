"""思忆集 MCP Server 入口（stdio）。

用法::

    python 思忆集app/mcp_server/server.py [--data-dir PATH]

* 不带 ``--data-dir`` → 默认生产数据目录（与后端同源）
* 带 ``--data-dir``  → 指定数据目录（测试/隔离用）

DSH profile 接入示例（cordis.patch.yml）::

    - id: mcp-siyi
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: siyi
        transport: stdio
        command: '<venv python.exe>'
        args: ['思忆集app/mcp_server/server.py']
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# 独立进程运行保证：无论从哪个 cwd 启动，都能 import 思忆集app 内模块
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from mcp_server.protocol import McpStdioServer  # noqa: E402
from mcp_server.store import SiyiStore  # noqa: E402
from mcp_server.tools import build_tool_specs  # noqa: E402
from mcp_server.tool_runner import ToolRunner  # noqa: E402

logger = logging.getLogger("siyi.mcp_server")


def _load_agent_kernel_from_store(store: SiyiStore) -> Any:
    """读取数据目录里 ``agent_kernel`` 原始值（dict / JSON 串 / None）。

    后端 settings 与 MCP 进程同源（同一 LocalDataStore 的 "settings" 键）。
    """
    try:
        settings = store.store.load_json("settings", {})
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(settings, dict):
        return None
    return settings.get("agent_kernel")


def _extract_mcp_tool_policy(agent_kernel_raw: Any) -> Any:
    """从 ``agent_kernel`` 原始值提取 ``mcp_tool_policy``（dict/JSON 串 → 子键）。"""
    if isinstance(agent_kernel_raw, dict):
        return agent_kernel_raw.get("mcp_tool_policy")
    if isinstance(agent_kernel_raw, str):
        try:
            parsed = json.loads(agent_kernel_raw)
        except (json.JSONDecodeError, ValueError):
            return None
        if isinstance(parsed, dict):
            return parsed.get("mcp_tool_policy")
    return None


def _load_mcp_tool_policy_from_store(store: SiyiStore) -> Any:
    """从数据目录读取 ``agent_kernel.mcp_tool_policy``（M5 工具策略层）。

    缺失/坏值 → None（默认 allow_all，行为与现状一致）。
    语义默认（``parse_mcp_tool_policy`` 结果为纯 allow_all、无 allow/deny/per_kernel）
    → 同样返回 None：保持现状无门控路径，行为逐字节一致。
    """
    from services.tooling import tool_policy

    raw = _extract_mcp_tool_policy(_load_agent_kernel_from_store(store))
    if raw is None:
        return None
    parsed = tool_policy.parse_mcp_tool_policy(raw)
    if parsed == tool_policy.parse_mcp_tool_policy(None):
        # 显式写的 allow_all 与缺省语义等价 → 与现状一致（无门控）
        return None
    return parsed


def _load_kernel_from_store(store: SiyiStore) -> str:
    """从数据目录解析当前生效内核身份（``--kernel`` 回退）。

    spawn 处（DSH 外部进程）拿不到 context 时，MCP 进程按同源 settings 回退：
    ``agent_kernel.type ∈ {dsh, claude_code, codex}`` → 对应网关名；否则空串
    （保持现状默认行为，per-kernel 工具策略不生效，仅全局策略）。
    """
    from services.tooling import tool_policy

    return tool_policy.resolve_kernel_identity(_load_agent_kernel_from_store(store))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Siyi personal data MCP server (stdio)")
    parser.add_argument("--data-dir", default=None, help="data directory override (default: production)")
    parser.add_argument("--kernel", default="", help="kernel identity this MCP instance serves (per-kernel tool policy)")
    parser.add_argument("--debug", action="store_true", help="log to stderr")
    args = parser.parse_args(argv)

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format="%(name)s %(levelname)s %(message)s")

    store = SiyiStore(data_dir=args.data_dir)
    # M5 工具策略层：读 agent_kernel.mcp_tool_policy（语义默认 → None → 默认 allow_all）
    policy = _load_mcp_tool_policy_from_store(store)
    if policy is not None:
        logger.info("MCP tool policy loaded: mode=%s", policy.get("mode") if isinstance(policy, dict) else policy)
    # 内核身份（--kernel）：优先显式传参（DSH profile 侧可注入）；缺省时按同源
    # settings 回退解析（agent_kernel.type → dsh/claude_code/codex），拿不到则空串
    # （保持现状默认行为：per-kernel 工具策略不生效）。
    kernel = args.kernel or _load_kernel_from_store(store)
    if kernel:
        logger.info("MCP kernel identity: %s", kernel)
    # 工具注册表与后端同源（思忆集app/data/tool_specs.json）：
    # 不带 --data-dir 时回退到应用数据目录，否则 MCP 会话中注册表永远为空。
    runner = ToolRunner(data_dir=args.data_dir or str(Path(__file__).resolve().parents[1] / "data"))
    server = McpStdioServer(name="siyi", version="0.1.0",
                            tools=build_tool_specs(store, runner=runner, policy=policy, kernel=kernel))
    server.serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
