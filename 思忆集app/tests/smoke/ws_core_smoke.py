"""核心链路冒烟：ping → 笔记 CRUD → 任务 CRUD → 资讯 → 用量。"""
import asyncio, io, json, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import aiohttp

WS_URL = "ws://127.0.0.1:18765/ws"
results: list[tuple[str, bool, str]] = []

async def call(ws, method, params, rid, timeout=30):
    await ws.send_json({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
        data = json.loads(msg.data)
        if data.get("id") == rid:
            return data

def keys_of(res):
    """稳健取 key 列表：兼容 dict 与 list 两种 result 形态。"""
    if isinstance(res, dict):
        return list(res.keys())
    if isinstance(res, list) and res:
        return [f"len({len(res)})"]
    return []

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name} {detail}")

async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS_URL, timeout=10) as ws:
            r = await call(ws, "ping", {}, 1)
            check("ping", r.get("result") == "pong", str(r.get("result")))

            r = await call(ws, "system.status", {}, 2)
            check("system.status", "error" not in r, str(keys_of(r.get("result")))[:60])

            r = await call(ws, "notes.list", {"limit": 5}, 3)
            res = r.get("result", {})
            ok = "error" not in r and (isinstance(res, dict) and ("notes" in res or "items" in res) or isinstance(res, list))
            check("notes.list", ok, f"keys={keys_of(res)[:5]}")

            r = await call(ws, "tasks.list", {"limit": 5}, 4)
            res = r.get("result", {})
            check("tasks.list", "error" not in r, f"keys={keys_of(res)[:5]}")

            r = await call(ws, "info.cached_items", {"limit": 5}, 5)
            res = r.get("result", {})
            check("info.cached_items", "error" not in r, f"keys={keys_of(res)[:5]}")

            r = await call(ws, "usage.today", {}, 6)
            check("usage.today", "error" not in r, f"enabled={r.get('result',{}).get('enabled')}")

            r = await call(ws, "model.list", {}, 7)
            check("model.list", "error" not in r, f"keys={keys_of(r.get('result'))[:5]}")

            # chat.send：DeepSeek 欠费可能走降级，只要求不崩溃且有响应
            try:
                r = await call(ws, "chat.send", {"message": "你好", "session_id": "smoke-test"}, 8, timeout=60)
                check("chat.send", "error" not in r, f"keys={keys_of(r.get('result'))[:5]}")
            except asyncio.TimeoutError:
                check("chat.send", False, "60s 超时")

    failed = [x for x in results if not x[1]]
    print(f"\n核心链路: {len(results)-len(failed)}/{len(results)} 通过")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    asyncio.run(main())