"""全方法契约冒烟：遍历后端所有注册方法，只读真实调用 / 写方法优雅拒绝。

方法清单通过 system.status 从服务端实时获取（比静态扫描更可靠）。
用法: python -X utf8 tests/smoke/ws_method_contract.py
输出: 每方法一行 ✅/⚠️/❌ + 末尾汇总；退出码 0=全部通过, 1=存在 ❌

健壮性设计：
- 后端对消息并发 create_task，同 ws 并发写不安全。慢请求（本地 LLM AI 方法）
  若触发客户端超时，会让客户端未消费完就发下一条 → 连接损坏 → 后续
  ClientConnectionResetError 雪崩。因此：
  1) TIMEOUT 提高到 90s（放行慢 AI 方法，避免误超时）
  2) call 失败为连接类错误（超时 / reset / 连接受损）时，重建连接隔离
     后再针对性重试一次，避免整批误报。
  3) PROBE_DELAY 节流避免触发限流 -32003。
"""
import asyncio, io, json, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # 思忆集app

import aiohttp

WS_URL = "ws://127.0.0.1:18765/ws"
TIMEOUT = 90.0
PROBE_DELAY = 0.45   # 节流，避免触发后端限流(默认 200 req/60s)
LIMIT_RETRY = 3      # 遇 -32003 的重试次数
CONN_RETRY = 2       # 连接类失败时重建连接的重试次数

# 已知写/破坏性/需外部依赖的方法：空参数调用，只断言"优雅拒绝"
WRITE_PREFIXES = (
    "notes.", "tasks.", "word.", "wordbook.", "inbox.", "transfer.", "transcribe.",
    "sandbox.", "confirmation.", "reminder.", "cron.", "task_queue.", "agent.",
    "sync.", "tool.", "api_keys.", "provider.", "audit.", "evolution.",
    "knowledge.", "reading.", "daily.", "data.", "settings.", "job_hunter.scrape",
    "job_hunter.resume", "job_hunter.jobs.set_status", "job_hunter.profile.set",
    "job_hunter.match", "job_hunter.analyze", "job_hunter.cold_message",
    "job_hunter.resume_advice", "job_hunter.interview_prep", "url_learning.",
    "rss.", "external_content.", "transcribe.", "trae.", "remote.", "process.",
    "maintenance.", "usage.purge", "usage.record", "chat.feedback",
    "kimi.", "notification.test", "notification.mark", "transfer.",
    "transcribe.", "file.", "ai.enhance",
)
# 明确只读（真实调用）
READ_PREFIXES = (
    "info.", "memory.", "knowledge.search", "knowledge.graph", "knowledge.relations",
    "knowledge.unified_query", "trace.", "system.", "provider.list", "provider.health",
    "usage.dashboard", "usage.today", "usage.budget.get", "usage.stats",
    "model.list", "capability.list", "timeline.", "notification.list",
    "notification.stats", "notification.preferences.get", "job_hunter.profile.get",
    "job_hunter.stats", "job_hunter.platforms", "job_hunter.jobs.list",
    "job_hunter.recommendations", "task_queue.stats", "task_queue.list",
    "task_queue.dead_letter.list", "audit.list", "audit.stats",
    "evolution.status", "evolution.last_briefing", "evolution.list_results",
    "weather.", "translate.languages", "word.list", "word.count_due", "tags.list",
    "sync.status", "ping", "siyi.ping", "calendar.", "chat.history",
)


def is_conn_error(detail: str) -> bool:
    """连接层错误：超时、连接重置、传输关闭——需重建连接隔离。"""
    return (detail == "TIMEOUT"
            or "ClientConnectionResetError" in detail
            or "closing transport" in detail
            or "concurrent write" in detail.lower()
            or "Connection pool is closed" in detail)


async def open_ws(session: aiohttp.ClientSession) -> aiohttp.ClientWebSocketResponse:
    return await session.ws_connect(WS_URL, timeout=10)


async def call(ws, method: str, params: dict, rid: int) -> tuple[bool, str]:
    await asyncio.sleep(PROBE_DELAY)
    try:
        await ws.send_json({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = await asyncio.wait_for(ws.receive(), timeout=TIMEOUT)
            if msg.type == aiohttp.WSMsgType.CLOSE or msg.type == aiohttp.WSMsgType.CLOSED:
                return False, "WS_CLOSED_BY_SERVER"
            data = json.loads(msg.data)
            if data.get("id") != rid:
                continue
            if "error" in data:
                err = data["error"]
                code = err.get("code") if isinstance(err, dict) else err
                return False, f"RPC error {code}: {str(err)[:120]}"
            return True, "ok"
    except asyncio.TimeoutError:
        return False, "TIMEOUT"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def do_probe(session, method: str, params: dict, rid: int, ws):
    """带限流重试的核心探测。返回 (ok_flag, detail, 是否需要重建连接)。"""
    await asyncio.sleep(PROBE_DELAY)
    for attempt in range(1, LIMIT_RETRY + 1):
        ok_flag, detail = await call(ws, method, params, rid)
        if ok_flag:
            return True, "ok", False
        if detail.startswith("RPC error -32003"):
            await asyncio.sleep(1.0 * attempt)   # 限流退避，非故障
            continue
        return ok_flag, detail, is_conn_error(detail)
    return False, "RATE_LIMIT_STILL: -32003 after retries", False


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS_URL, timeout=10) as ws0:
            await ws0.send_json({"jsonrpc": "2.0", "id": 999901, "method": "system.status", "params": {}})
            methods: list[str] = []
            while True:
                msg = await asyncio.wait_for(ws0.receive(), timeout=TIMEOUT)
                data = json.loads(msg.data)
                if data.get("id") == 999901:
                    methods = (data.get("result") or {}).get("methods") or []
                    break
            print(f"共从服务端获取 {len(methods)} 个方法")

        ok, graceful, broken, skipped = 0, 0, [], 0
        ws = await open_ws(s)
        rid = 0
        for method in methods:
            rid += 1
            # 逐方法稳健探测；连接损坏则重建隔离，避免雪崩误报
            ok_flag, detail, need_rebuild = await do_probe(s, method, {}, rid, ws)
            if not ok_flag and (need_rebuild or detail in ("WS_CLOSED_BY_SERVER",)):
                # 关掉损坏连接，隔离后在新连接上重试一次
                for _ in range(CONN_RETRY):
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    ws = await open_ws(s)
                    ok_flag, detail, _ = await do_probe(s, method, {}, rid, ws)
                    if ok_flag or detail == "WS_CLOSED_BY_SERVER":
                        break
                    if not is_conn_error(detail):
                        break

            if method.startswith(READ_PREFIXES):
                if ok_flag:
                    ok += 1
                    print(f"  ✅ {method}")
                else:
                    broken.append((method, detail))
                    print(f"  ❌ {method}: {detail}")
            elif method.startswith(WRITE_PREFIXES):
                if ok_flag:
                    graceful += 1
                    print(f"  ⚠️ {method} (空参无异常)")
                else:
                    broken.append((method, detail))
                    print(f"  ❌ {method}: {detail}")
            else:
                skipped += 1
                if ok_flag:
                    print(f"  · {method}")
                elif detail.startswith("RPC error -32603"):
                    broken.append((method, detail))
                    print(f"  ❌ {method}: {detail}")
                else:
                    # 参数校验类优雅拒绝（-32600/2/1）不算 bug
                    print(f"  · {method} ({detail[:60]})")

        try:
            await ws.close()
        except Exception:
            pass

        print("\n===== 汇总 =====")
        print(f"✅ 只读通过: {ok}  ⚠️ 写方法优雅处理: {graceful}  其它跳过: {skipped}")
        if broken:
            print(f"❌ 失败 {len(broken)} 个（真实 bug）:")
            for m, d in broken:
                print(f"   {m}: {d}")
            sys.exit(1)
        print("全部方法契约通过")


if __name__ == "__main__":
    asyncio.run(main())