"""复现 chat.send 流式中断：打印每一帧的时间戳、类型与最终关闭码。"""
import asyncio
import json
import sys
import time

import aiohttp

URL = "ws://127.0.0.1:18765/ws"


async def main():
    t0 = time.monotonic()

    def ts() -> str:
        return f"+{time.monotonic() - t0:6.1f}s"

    timeout = aiohttp.ClientTimeout(total=180, sock_read=150)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(URL, heartbeat=None) as ws:
            print(f"[{ts()}] 已连接")
            await ws.send_str(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "chat.send",
                "stream": True,
                "params": {"message": "你好", "provider": "qwen3.8-flash",
                           "model": "qwen3.8-flash", "stream": True},
            }))
            n = 0
            first_chunk_at = None
            last_chunk_at = None
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    n += 1
                    now = time.monotonic() - t0
                    try:
                        d = json.loads(msg.data)
                    except Exception:
                        print(f"[{ts()}] 非JSON帧: {msg.data[:120]!r}")
                        continue
                    if d.get("id") == 1 and "error" in d:
                        print(f"[{ts()}] JSON-RPC error 响应: {json.dumps(d['error'], ensure_ascii=False)[:400]}")
                        continue
                    chunk = d.get("chunk", d)
                    ctype = chunk.get("type", "?") if isinstance(chunk, dict) else "?"
                    text = ""
                    if isinstance(chunk, dict):
                        text = str(chunk.get("content") or chunk.get("message") or "")[:60]
                    gap = f" (距上帧 {now - last_chunk_at:.1f}s)" if last_chunk_at else ""
                    if first_chunk_at is None:
                        first_chunk_at = now
                        print(f"[{ts()}] 首帧 type={ctype}{gap} {text!r}")
                    elif ctype in ("error", "done") or n % 10 == 0:
                        print(f"[{ts()}] 帧#{n} type={ctype}{gap} {text!r}")
                    last_chunk_at = now
                    if ctype == "done":
                        print(f"[{ts()}] 流正常结束, 共 {n} 帧")
                        break
                    if ctype == "error":
                        print(f"[{ts()}] 流 error chunk: {text!r}")
                        break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    print(f"[{ts()}] ★ 连接被关闭 CLOSED code={ws.close_code} reason={msg.extra!r}")
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"[{ts()}] ★ 连接错误: {ws.exception()!r} code={ws.close_code}")
                    break
            print(f"[{ts()}] 结束: 共收 {n} 帧, 首帧 {first_chunk_at}, 末帧间隔 {time.monotonic() - t0 - (last_chunk_at or 0):.1f}s, close_code={ws.close_code}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
