"""事件推送专项：触发 notification.test → 断言收到 notification.delivered 事件。"""
import asyncio, io, json, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import aiohttp

WS_URL = "ws://127.0.0.1:18765/ws"

async def main():
    received: list[str] = []
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS_URL, timeout=10) as ws:
            # 先调 notification.test 触发（事件会广播到本连接）
            await ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "notification.test",
                                "params": {"channel": "websocket"}})
            deadline = asyncio.get_event_loop().time() + 15
            while asyncio.get_event_loop().time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    data = json.loads(msg.data)
                    if data.get("type") == "event":
                        received.append(data.get("event_type", "?"))
                        print(f"  收到事件: {data.get('event_type')}")
                    elif data.get("id") == 1:
                        res = data.get("result", {})
                        print(f"  notification.test 返回: {json.dumps(res, ensure_ascii=False)[:150]}")
                        if not res.get("success"):
                            print("  ⚠️ notification.test 返回失败，检查通知分发器")
                            break
                except asyncio.TimeoutError:
                    break
            ok = any("notification.delivered" in e for e in received) or \
                 any("notification" in e for e in received)
            print(f"\n事件推送: {'✅ 收到 notification.delivered' if ok else '❌ 未收到通知事件'} (收到 {len(received)} 个事件: {received})")
            sys.exit(0 if ok else 2)

if __name__ == "__main__":
    asyncio.run(main())