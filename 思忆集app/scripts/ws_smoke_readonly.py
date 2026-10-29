#!/usr/bin/env python3
"""WS 只读冒烟：对运行中的后端逐个调用 16 个只读 RPC 方法，全部无 error 即 PASS。

转正说明：原脚本曾是 `.pytest-tmp/ws_smoke_readonly.py` 临时产物（随目录丢失），
2026-09-13 依 AGENTS 测试基线表重建为正式脚本。零写入（全部只读方法），
可用于发版前冒烟 / 后端启动后人工验证 / CI 手动触发。

用法：
  python scripts/ws_smoke_readonly.py                     # ws://127.0.0.1:18765/ws
  SIYI_WS_PORT=18799 python scripts/ws_smoke_readonly.py  # 指定端口（避开占用窗口）
  python scripts/ws_smoke_readonly.py ws://127.0.0.1:18765/ws  # 显式 URL

认证：loopback 匿名连接由后端 _verify_auth 本机信任分支放行；远程地址需在
环境变量 SIYI_DIAG_TOKEN 提供有效 token（拼 ?token= 查询参数）。

退出码：0 = 全部通过；1 = 存在失败（方法报错/超时/连接失败）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse

TIMEOUT_S = float(os.environ.get("SIYI_SMOKE_TIMEOUT", "15"))

# 只读方法（16 个，跨域抽查；不含任何破坏性/R4 方法）
READONLY_METHODS = [
    "ping",
    "siyi.ping",
    "system.info",
    "system.status",
    "notes.list",
    "tasks.list",
    "reminder.list",
    "tags.list",
    "wordbook.list",
    "cron.status",
    "model.list",
    "capability.list",
    "memory.recent",
    "usage.today",
    "sync.status",
    "notification.stats",
]


def _default_url() -> str:
    url = os.environ.get("SIYI_WS_URL", "").strip()
    if url:
        return url
    host = os.environ.get("SIYI_WS_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = os.environ.get("SIYI_WS_PORT", "18765").strip() or "18765"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"ws://{host}:{port}/ws"


def _with_token(url: str) -> str:
    token = os.environ.get("SIYI_DIAG_TOKEN", "").strip()
    if not token:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}token={urllib.parse.quote(token)}"


async def _call_all(url: str) -> list[tuple[str, bool, str]]:
    import websockets  # 延迟导入：缺依赖时给出明确报错

    results: list[tuple[str, bool, str]] = []
    async with websockets.connect(_with_token(url), open_timeout=TIMEOUT_S) as ws:
        for i, method in enumerate(READONLY_METHODS, 1):
            try:
                await ws.send(json.dumps(
                    {"jsonrpc": "2.0", "id": i, "method": method, "params": {}}
                ))
                note = ""
                ok = False
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT_S)
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    msg = json.loads(raw)
                    # 跳过事件帧（EventBridge 推送），只认本次请求的响应
                    if msg.get("type") == "event" or ("event_type" in msg and "id" not in msg):
                        continue
                    if msg.get("id") != i:
                        continue
                    if "error" in msg:
                        note = f"error {msg['error'].get('code')}: {msg['error'].get('message', '')[:80]}"
                    elif "result" in msg:
                        ok = True
                        note = "ok"
                    else:
                        note = "响应缺 result/error"
                    break
            except Exception as exc:  # noqa: BLE001 —— 冒烟要汇总而非中断
                ok, note = False, f"{type(exc).__name__}: {str(exc)[:80]}"
            results.append((method, ok, note))
    return results


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else _default_url()
    print(f"[ws-smoke] 目标 {url}，{len(READONLY_METHODS)} 个只读方法")
    try:
        results = asyncio.run(_call_all(url))
    except Exception as exc:  # noqa: BLE001
        print(f"[ws-smoke] FAIL 连接失败: {type(exc).__name__}: {exc}")
        return 1

    failed = 0
    for method, ok, note in results:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  [{mark}] {method:24s} {note}")
    print(f"[ws-smoke] {'PASS' if failed == 0 else 'FAIL'}：{len(results) - failed}/{len(results)} 通过")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
