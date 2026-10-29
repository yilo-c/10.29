"""区分实验：默认路由 vs qwen3.8-flash 的 chat.send 首帧延迟。"""
import asyncio
import json
import time

import aiohttp

URL = "ws://127.0.0.1:18765/ws"


async def one(ws, label: str, provider: str | None, model: str | None, rid: int, budget: float = 60.0) -> None:
    params: dict = {"message": "你好", "stream": True}
    if provider:
        params["provider"] = provider
        params["model"] = model
    t0 = time.monotonic()
    await ws.send_str(json.dumps({"jsonrpc": "2.0", "id": rid, "method": "chat.send", "stream": True, "params": params}))
    first = None
    types_seen: dict[str, int] = {}
    try:
        async with asyncio.timeout(budget):
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    print(f"[{label}] 帧异常 {msg.type} code={ws.close_code}")
                    return
                d = json.loads(msg.data)
                if d.get("id") == rid and "error" in d:
                    print(f"[{label}] JSON-RPC error: {json.dumps(d['error'], ensure_ascii=False)[:250]}")
                    return
                if d.get("id") != rid:
                    continue
                chunk = d.get("chunk", {})
                ctype = chunk.get("type", "text") if isinstance(chunk, dict) else "?"
                types_seen[ctype] = types_seen.get(ctype, 0) + 1
                if first is None:
                    first = time.monotonic() - t0
                    print(f"[{label}] 首帧 {ctype!r} 用时 {first:.1f}s")
                if ctype in ("done", "error"):
                    err = str(chunk.get("content", ""))[:150] if ctype == "error" else ""
                    print(f"[{label}] {ctype} 总用时 {time.monotonic() - t0:.1f}s 帧统计={types_seen} {err}")
                    return
    except TimeoutError:
        print(f"[{label}] ★ {budget:.0f}s 内零帧（挂起） 已见={types_seen}")
    except Exception as e:
        print(f"[{label}] 异常: {type(e).__name__}: {e} 已见={types_seen}")


async def main() -> None:
    timeout = aiohttp.ClientTimeout(total=200, sock_read=190)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(URL, heartbeat=None) as ws:
            print("== A: 默认路由（不带 provider） ==")
            await one(ws, "A默认", None, None, 11)
            print("== B: provider=qwen3.8-flash ==")
            await one(ws, "B千问", "qwen3.8-flash", "qwen3.8-flash", 12)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
