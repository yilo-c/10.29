# -*- coding: utf-8 -*-
"""V1 随机 RPC 模糊测试（item 4：过夜长稳的骨架版）。

用法（仓库根，venv）：
    .venv3.11/Scripts/python.exe 思忆集app/scripts/v1_fuzz.py --minutes 480   # 过夜 8h
    .venv3.11/Scripts/python.exe 思忆集app/scripts/v1_fuzz.py --minutes 5     # 冒烟

做什么：
  起隔离后端 → 以随机间隔连打「类型感知的随机参数」RPC → 断言每笔请求
  5s 内有类型响应（result 或 JSON-RPC error 均可，超时/断连 = 命中）→
  结束时输出 trace.stats 与命中清单。

判定口径：模糊测试的目标不是「全部成功」，而是「永不挂起、永不崩溃、
永不吐裸 traceback」——错误要分型可见（V1 ⑦）。
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import aiohttp

BACKEND_DIR = Path(__file__).resolve().parents[1]
RPC_TIMEOUT_S = 5.0

# 种子方法池（读多写少，覆盖各域；写方法用明显的 fuzz 前缀防脏数据扩散）
SEED_METHODS = [
    ("ping", {}),
    ("siyi.ping", {}),
    ("system.info", {}),
    ("system.status", {}),
    (
        "notes.list",
        {
            "limit": "LIMIT",
        },
    ),
    ("notes.get", {"note_id": "INT"}),
    ("notes.search", {"query": "STR"}),
    ("tasks.list", {"limit": "INT"}),
    ("tasks.get", {"task_id": "INT"}),
    ("inbox.list", {}),
    ("inbox.add", {"content": "STR"}),
    ("wordbook.list", {}),
    ("memory.recent", {"limit": "INT"}),
    ("memory.search", {"query": "STR"}),
    ("tags.list", {}),
    ("translations.list", {}),
    ("reminder.list", {}),
    ("timeline.list", {"limit": "INT"}),
    ("timeline.stats", {"days": "INT"}),
    ("trace.list", {"limit": "INT"}),
    ("trace.stats", {"days": "INT"}),
    ("audit.list", {"limit": "INT"}),
    ("audit.stats", {}),
    ("usage.today", {}),
    ("usage.dashboard", {"days": "INT"}),
    ("sync.status", {}),
    ("cron.status", {}),
    ("cron.list", {}),
    ("notification.list", {"limit": "INT"}),
    ("notification.stats", {}),
    ("capability.list", {}),
    ("info.cached_items", {"limit": "INT"}),
    ("knowledge.search", {"query": "STR"}),
    ("weather.current", {}),
    ("chat.feedback.stats", {"days": "INT"}),
    ("notes.create", {"title": "STR", "content": "STR"}),
    ("tasks.create", {"title": "STR"}),
    ("notes.update", {"note_id": "INT", "content": "STR"}),
    ("xiaowen.quote", {}),
]

# 非法值池：SQL/JSON/超长/类型错配/负数/深度嵌套
EVIL_STRINGS = [
    "'; DROP TABLE kv_store;--",
    "<script>alert(1)</script>",
    "../../etc/passwd",
    "x" * 100_000,
    "\udce4\udcb8\udcad",
    "\n".join(["line"] * 500),
    '{"$gt": 1}',
    "%s%s%s%n",
    "你好" * 3000,
]


def _fuzz_param(kind: str, rng: random.Random):
    if kind == "INT":
        return rng.choice([0, 1, 5, -1, 999999, 2**53, "abc", None, 3.7])
    if kind == "LIMIT":
        return rng.choice([1, 10, 50, -5, 0, 10**9, "10"])
    if kind == "STR":
        return rng.choice(EVIL_STRINGS) + rng.choice(["", "-" + rng.randint(0, 99).__str__()])
    return None


async def _call(ws, rid, method, params, timeout=RPC_TIMEOUT_S):
    await ws.send_json({"jsonrpc": "2.0", "id": rid, "method": method, "params": params, "client_version": "1.2.0"})
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = await ws.receive(timeout=max(0.1, deadline - time.time()))
        if msg.type != aiohttp.WSMsgType.TEXT:
            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                raise ConnectionError("ws closed during call")
            continue
        data = json.loads(msg.data)
        if data.get("id") == rid:
            if "error" in data:
                return "typed_error", f"-{data['error'].get('code')}"
            return "ok", ""
    raise TimeoutError(method)


async def fuzz_loop(port: int, token: str, minutes: float, rng: random.Random) -> dict:
    stats = {"ok": 0, "typed_error": 0, "timeout": 0, "closed": 0, "total": 0}
    hits: list[str] = []
    deadline = time.time() + minutes * 60
    rid = 0
    conn_ok = True
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        ws = None
        while time.time() < deadline:
            try:
                if ws is None or ws.closed:
                    ws = await session.ws_connect(f"ws://127.0.0.1:{port}/ws?token={token}")
                    conn_ok = True
                method, base = rng.choice(SEED_METHODS)
                params = {k: _fuzz_param(v, rng) if v else v for k, v in base.items()}
                rid += 1
                stats["total"] += 1
                kind, note = await _call(ws, rid, method, params)
                stats[kind] = stats.get(kind, 0) + 1
                if kind == "timeout":
                    hits.append(f"TIMEOUT {method} {json.dumps(params, ensure_ascii=False)[:80]}")
                    print(f"  ⚠️ {hits[-1]}")
            # 修复（2026-09-13 长稳实测崩溃）：aiohttp.WSCloseCode 是 IntEnum
            # （继承 int，不继承 BaseException），放进 except 元组一遇连接错误
            # 即 TypeError 自爆。改用真实异常族：ClientError 覆盖握手/连接失败，
            # OSError 覆盖底层 socket（ConnectionError 是其子类），超时单独列。
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, RuntimeError) as exc:
                stats["closed"] += 1
                hits.append(f"CLOSED {type(exc).__name__}: {str(exc)[:60]}")
                print(f"  ⚠️ {hits[-1]}")
                conn_ok = False
                ws = None
                await asyncio.sleep(2)  # 退避后重连（断线重连语义本身也是被测项）
            except (TimeoutError, asyncio.TimeoutError) as exc:
                stats["timeout"] += 1
                hits.append(f"TIMEOUT {str(exc)[:60]}")
                print(f"  ⚠️ {hits[-1]}")
            await asyncio.sleep(rng.uniform(0.05, 0.4))
    return {"stats": stats, "hits": hits, "reconnected": conn_ok}


async def run(minutes: float) -> int:
    data_dir = Path(tempfile.mkdtemp(prefix="siyi_fuzz_"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ)
    for k in ("DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "ANTHROPIC_API_KEY", "SIYI_LLM_API_KEY"):
        env.pop(k, None)
    env.update(SIYI_WS_HOST="127.0.0.1", SIYI_WS_PORT=str(port), SIYI_DATA_DIR=str(data_dir), SIYI_DISABLE_DESKNOTES_BRIDGE="1")
    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.headless_main"], cwd=str(BACKEND_DIR), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        token = ""
        t0 = time.time()
        while time.time() - t0 < 60:
            tf = data_dir / "auth_token"
            if tf.exists():
                token = tf.read_text(encoding="utf-8", errors="replace").strip()
                try:
                    with urllib_request_health(port, token) as r:
                        if r.status == 200:
                            break
                except Exception:
                    pass
            time.sleep(0.5)
        else:
            print("后端启动超时")
            return 1
        rng = random.Random(int(time.time()))
        print(f"模糊测试开始：{minutes:g} 分钟, 端口 {port}, 种子方法 {len(SEED_METHODS)} 个")
        result = await fuzz_loop(port, token, minutes, rng)
        print("\n==== 统计 ====")
        print(json.dumps(result["stats"], ensure_ascii=False, indent=2))
        # 收尾：trace.stats（后端自己的观测口径）
        verdict = "🟢 未命中静默失败" if result["stats"]["timeout"] == 0 and result["stats"]["closed"] <= 2 else f"🔴 命中 {len(result['hits'])} 次"
        print(f"\n结论: {verdict}")
        for h in result["hits"][:20]:
            print("  -", h)
        return 0 if result["stats"]["timeout"] == 0 else 1
    finally:
        proc.kill()
        proc.wait()


def urllib_request_health(port: int, token: str):
    import urllib.request

    return urllib.request.urlopen(f"http://127.0.0.1:{port}/health?token={token}", timeout=2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10.0)
    args = ap.parse_args()
    return asyncio.run(run(args.minutes))


if __name__ == "__main__":
    sys.exit(main())
