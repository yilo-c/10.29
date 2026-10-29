# -*- coding: utf-8 -*-
"""WS JSON-RPC 只读冒烟测试：验证核心方法在真实后端上可用。"""

import asyncio
import json
import sys
from pathlib import Path

import aiohttp

# V2.1 批次1#4：接入统一 WS 认证（后台已强制认证，无 token 连接会被拒）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ws_diag_auth  # noqa: E402

URL = "ws://127.0.0.1:18765/ws"

# (method, params) — 全部只读
CASES = [
    ("ping", {}),
    ("siyi.ping", {}),
    ("notes.list", {"limit": 3}),
    ("tasks.list", {"limit": 3}),
    ("inbox.list", {}),
    ("tags.list", {}),
    ("wordbook.list", {}),
    ("memory.recent", {"limit": 3}),
    ("translate.languages", {}),
    ("chat.list_conversations", {"limit": 3}),
    ("notification.list", {"limit": 3}),
    ("notification.stats", {}),
    ("chat.feedback.stats", {}),
    ("info.items.stats", {}),
    ("translations.list", {}),
    ("system.info", {}),
]


async def main() -> int:
    failures = []
    results: dict = {}
    async with aiohttp.ClientSession() as session:
        async with ws_diag_auth.ws_connect(session, URL, timeout=aiohttp.ClientTimeout(total=15)) as ws:
            pending = {}
            for idx, (method, params) in enumerate(CASES, start=1):
                await ws.send_json({"jsonrpc": "2.0", "id": idx, "method": method, "params": params})
                pending[idx] = method
            deadline = asyncio.get_event_loop().time() + 60
            while pending and asyncio.get_event_loop().time() < deadline:
                try:
                    resp = json.loads(await asyncio.wait_for(ws.receive_str(), timeout=15))
                except asyncio.TimeoutError:
                    break
                if resp.get("type") == "event":
                    continue  # 服务端推送事件，非请求回包
                rid = resp.get("id")
                if rid in pending:
                    results[rid] = resp
                    del pending[rid]
            for idx, method in pending.items():
                failures.append((method, "timeout: 无回包"))
                print(f"[FAIL] {method}: timeout")
            for idx, (method, params) in enumerate(CASES, start=1):
                if idx in results:
                    resp = results[idx]
                    if "error" in resp:
                        failures.append((method, f"RPC error: {resp['error']}"))
                        print(f"[FAIL] {method}: {resp['error']}")
                    else:
                        result = resp.get("result")
                        preview = json.dumps(result, ensure_ascii=False)[:80] if result is not None else "None"
                        if isinstance(result, dict) and "error" in result:
                            failures.append((method, f"result内嵌 error: {result['error']}"))
                            print(f"[FAIL] {method}: {result['error']}")
                        else:
                            print(f"[ OK ] {method} -> {preview}")
    print()
    if failures:
        print(f"共 {len(failures)} 个失败:")
        for m, e in failures:
            print(f"  {m}: {e}")
        return 1
    print(f"全部 {len(CASES)} 个只读方法冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
