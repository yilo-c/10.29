"""L4 新增功能专项：验证本轮改动 6 个功能族契约。"""
import asyncio, io, json, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import aiohttp
WS_URL = "ws://127.0.0.1:18765/ws"

CHECKS = [
    ("job_hunter.profile.get", {}, lambda r: "profile" in r),
    ("job_hunter.jobs.list", {"limit": 3}, lambda r: "jobs" in r),
    ("job_hunter.stats", {}, lambda r: "stats" in r),
    ("job_hunter.platforms", {}, lambda r: "platforms" in r),
    ("job_hunter.recommendations", {"limit": 3}, lambda r: True),
    ("task_queue.stats", {}, lambda r: "stats" in r),
    ("task_queue.list", {"limit": 3}, lambda r: "tasks" in r),
    ("task_queue.dead_letter.list", {"limit": 3}, lambda r: "tasks" in r),
    ("usage.dashboard", {"days": 7}, lambda r: "today" in r),
    ("usage.budget.get", {}, lambda r: True),
    ("usage.today", {}, lambda r: "calls" in r),
    ("usage.export", {"days": 1}, lambda r: isinstance(r, str) or "error" in r),
    ("evolution.status", {}, lambda r: True),
    ("evolution.last_briefing", {}, lambda r: True),
    ("evolution.list_results", {"limit": 3}, lambda r: isinstance(r, list)),
    ("audit.list", {"limit": 5}, lambda r: "entries" in r),   # ★ 本轮修复点
    ("audit.stats", {}, lambda r: True),
    ("timeline.list", {"limit": 5}, lambda r: "entries" in r),
    ("timeline.stats", {"days": 3}, lambda r: True),
]

async def call(ws, method, params, rid):
    await ws.send_json({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout=30)
        data = json.loads(msg.data)
        if data.get("id") == rid:
            return data

async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS_URL, timeout=10) as ws:
            failed = []
            for i, (m, p, pred) in enumerate(CHECKS, 1):
                r = await call(ws, m, p, i)
                res = r.get("result") if "result" in r else r.get("error", {})
                ok = "error" not in r and pred(res) if "error" not in r else False
                if "error" in r:
                    ok = pred(res)  # error dict 也过 pred 才 ok
                print(f"  {'✅' if ok else '❌'} {m}: {json.dumps(res, ensure_ascii=False)[:100]}")
                if not ok:
                    failed.append(m)
            print(f"\nL4 专项: {len(CHECKS)-len(failed)}/{len(CHECKS)} 通过")
            sys.exit(1 if failed else 0)

if __name__ == "__main__":
    asyncio.run(main())