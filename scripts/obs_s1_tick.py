# -*- coding: utf-8 -*-
"""S1 观测每日 tick：探测运行中的后端 kernel.status，向 docs/obs-s1-log.md 追加一行。

用法:
    python scripts/obs_s1_tick.py [--host 127.0.0.1] [--port 18765] [--log PATH]

设计：纯只读探测，不修改后端任何状态，可安全接入 system.self_maintenance 每日 cron。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import os
import re
import sys

try:
    import aiohttp
except ImportError:  # pragma: no cover
    print("aiohttp 未安装，无法运行 S1 tick", file=sys.stderr)
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


def _parse_status(status: dict) -> tuple[bool, bool, int]:
    degraded = bool(status.get("degraded"))
    gateway_present = status.get("gateway_present")
    effective = status.get("effective_engine")
    gateway_active = (not degraded) and (effective == "gateway" or gateway_present)
    fatal = 1 if degraded else 0
    return bool(gateway_active), degraded, fatal


_ROW_RE = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(yes|no)\s*\|")


def _update_log(log_path: str, date: str, active: bool, degraded: bool, fatal: int) -> int:
    """追加/更新当日行，返回当前连续 active 天数。"""
    rows: list[str] = []
    today_touched = False
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                m = _ROW_RE.match(line)
                if m:
                    d = m.group(1)
                    if d == date:
                        rows.append(
                            f"| {date} | {'yes' if active else 'no'} | {str(degraded).lower()} | {fatal} | {'1' if not today_touched else ''}"
                        )
                        today_touched = True
                        continue
                    rows.append(line.rstrip("\n"))
                else:
                    rows.append(line.rstrip("\n"))
    # 重算连续 active 天数（基于表格末位）
    last_streak = 0
    for line in rows:
        m = _ROW_RE.match(line)
        if not m:
            continue
        if m.group(2) == "yes":
            last_streak += 1
        else:
            last_streak = 0
    if not today_touched:
        rows.append(f"| {date} | {'yes' if active else 'no'} | {str(degraded).lower()} | {fatal} | {last_streak + 1} |")
        last_streak += 1
    # 用真实连续值回写最后一行的连续列
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    return last_streak


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument(
        "--log",
        default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "docs",
            "obs-s1-log.md",
        ),
    )
    args = ap.parse_args()

    try:
        resp = await _rpc(args.host, args.port, "kernel.status")
    except Exception as exc:  # noqa: BLE001
        print(f"[S1] 探测失败: {exc}", file=sys.stderr)
        return 3

    status = resp.get("result", resp)
    active, degraded, fatal = _parse_status(status)
    date = _dt.date.today().isoformat()
    streak = _update_log(args.log, date, active, degraded, fatal)
    print(f"[S1] {date} gateway_active={active} degraded={degraded} gateway_fatal={fatal} streak={streak}")
    if not active or fatal > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
