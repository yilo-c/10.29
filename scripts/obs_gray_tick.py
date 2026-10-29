# -*- coding: utf-8 -*-
"""T4.1 subprocess 隔离灰度遥测 tick：探测 system.health.capability_routing，向日志追加一行。

用法:
    python scripts/obs_gray_tick.py [--host 127.0.0.1] [--port 18765] [--log PATH]

设计：纯只读探测，不修改后端任何状态。观察入口用于灰度观察期（≥1 周）量化
plugin / fallback 命中与插件错误，支撑 T4.1 转正决策。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import os
import sys

try:
    import aiohttp
except ImportError:  # pragma: no cover
    print("aiohttp 未安装，无法运行灰度遥测 tick", file=sys.stderr)
    sys.exit(2)

# V2.1 批次1#4：接入统一 WS 认证（后台已强制认证）。
import ws_diag_auth  # noqa: E402


async def _rpc(host: str, port: int, method: str, params: dict | None = None) -> dict:
    url = f"ws://{host}:{port}/ws"
    async with aiohttp.ClientSession() as session:
        async with ws_diag_auth.ws_connect(session, url, max_msg_size=16 * 1024 * 1024) as ws:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params or {},
            }
            await ws.send_str(json.dumps(payload))
            msg = await ws.receive()
            if msg.type != aiohttp.WSMsgType.TEXT:
                raise RuntimeError(f"非预期 WS 消息: {msg.type}")
            return json.loads(msg.data)


def _find_subsystem(snapshot: dict) -> dict | None:
    for sub in snapshot.get("subsystems", []):
        if sub.get("key") == "capability_routing":
            return sub
    return None


def _compress(stats: dict) -> str:
    """per_capability 压缩为 `cap:ph/fh/pe;cap:...` 一列。"""
    if not stats:
        return ""
    parts = []
    for cap in sorted(stats):
        v = stats[cap]
        parts.append(f"{cap}:{v.get('plugin_hits', 0)}/{v.get('fallback_hits', 0)}/{v.get('plugin_errors', 0)}")
    return ";".join(parts)


_HEADERS = (
    "| 时间 | plugin_hits | fallback_hits | plugin_errors | 已跟踪 caps | "
    "plugin 化 caps | per_capability(plugin/fallback/error) |\n"
    "|------|-------------|---------------|---------------|-------------|"
    "----------------|----------------------------------------|"
)


def _update_log(log_path: str, row: str) -> None:
    if not os.path.exists(log_path):
        header = (
            "# subprocess 隔离灰度遥测账本 (obs-gray-isolation-log)\n\n"
            "> 用途：T4.1 灰度观察期（≥1 周）记录 capability_routing 的 plugin / fallback\n"
            "> 命中与插件错误，作为 **SIYI_PLUGIN_ISOLATION=subprocess** 转正的量化依据。\n"
            "> 由 `python scripts/obs_gray_tick.py` 每次运行追加一行（纯只读探测，不改变后端状态）。\n\n"
            "## 观察基线\n"
            "- 灰度模式: `SIYI_PLUGIN_ISOLATION=subprocess`（`plugins.sandbox.choose_isolation_mode` 切换）\n"
            "- 观察实例: 无头后端 `ws://0.0.0.0:18765`，经 `system.health` 的 `capability_routing` 子系统采样（10s 聚合缓存）\n"
            "- 已装配 plugin invoker: 见下方 `plugin 化 caps` 列（启动时 `Registered N plugin tools`）\n\n"
            "## 转正验收门（T4.1 达成条件）\n"
            "- 观察期 ≥ 1 周，期间 `plugin_errors` 保持 0 且 info 链路业务无回退；\n"
            "- `plugin_hits` 随真实业务流量增长；冒烟/装配闭环脚本（`_smoke_gray_isolation.py` / `_verify_gray_wiring.py`）持续通过；\n"
            "- 达成后移除 `start_backend.bat` 的灰度开关或默认切换，并归档 `plugins/sandbox.py` 的 sandbox 直执行路径。\n\n"
            "## tick 记录\n"
        )
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(header + _HEADERS + "\n")
    with open(log_path, "r", encoding="utf-8") as fh:
        content = fh.read()
    if _HEADERS not in content:
        content = content.rstrip("\n") + "\n" + _HEADERS + "\n"
    content = content.rstrip("\n") + "\n" + row + "\n"
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(content)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument(
        "--log",
        default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "docs",
            "obs-gray-isolation-log.md",
        ),
    )
    args = ap.parse_args()

    try:
        resp = await _rpc(args.host, args.port, "system.health")
    except Exception as exc:  # noqa: BLE001
        print(f"[GRAY] 探测失败: {exc}", file=sys.stderr)
        return 3

    result = resp.get("result", resp)
    sub = _find_subsystem(result)
    if sub is None:
        print("[GRAY] capability_routing 子系统缺失（可能非 subprocess 灰度实例）", file=sys.stderr)
        return 2

    details = sub.get("details", {})
    total_plugin = details.get("total_plugin_hits", 0)
    total_fallback = details.get("total_fallback_hits", 0)
    total_errors = details.get("total_plugin_errors", 0)
    tracked = details.get("tracked_capabilities", 0)
    plugin_caps = details.get("plugin_enabled_capabilities", 0)
    per_cap = details.get("per_capability", {})

    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    row = f"| {now} | {total_plugin} | {total_fallback} | {total_errors} | {tracked} | {plugin_caps} | {_compress(per_cap)} |"
    _update_log(args.log, row)
    print(f"[GRAY] {now} plugin_hits={total_plugin} fallback_hits={total_fallback} plugin_errors={total_errors} caps={tracked}/{plugin_caps}")
    if not sub.get("available", False) or total_errors > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
