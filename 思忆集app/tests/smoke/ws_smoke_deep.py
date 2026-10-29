"""Deep smoke test: WS JSON-RPC methods — valid + invalid params, TIMEOUT, -32603."""

import asyncio
import io
import json
import aiohttp
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

WS_URL = "ws://127.0.0.1:18765/ws"
TIMEOUT = 8.0  # seconds
REQUEST_ID = 0

results = []


async def call(method, params=None, description=""):
    global REQUEST_ID
    REQUEST_ID += 1
    rid = REQUEST_ID
    payload = {
        "jsonrpc": "2.0",
        "id": rid,
        "method": method,
        "params": params if params is not None else {},
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(WS_URL, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as ws:
                await ws.send_json(payload)
                msg = await asyncio.wait_for(ws.receive_json(), timeout=TIMEOUT)
                return method, params, description, msg, None
    except asyncio.TimeoutError:
        return method, params, description, None, "TIMEOUT (>8s hang)"
    except Exception as e:
        return method, params, description, None, f"CLIENT_ERROR: {e}"


async def main():
    methods = []

    # --- ping ---
    methods.append(("ping", {}, "empty params"))
    methods.append(("ping", {"extra": 1}, "extra param"))
    methods.append(("ping", None, "null params"))

    # --- model.list ---
    methods.append(("model.list", {}, "empty params"))

    # --- pattern.execute (needs pattern name, with/without input) ---
    methods.append(("pattern.execute", {}, "empty params"))
    methods.append(("pattern.execute", {"pattern": "nonexistent"}, "bad pattern name"))
    methods.append(("pattern.execute", {"pattern": "summarize"}, "summarize pattern, no input"))
    methods.append(("pattern.execute", {"pattern": "not_a_real_pattern_xyz"}, "unregistered pattern"))

    # --- ai.parse_intent ---
    methods.append(("ai.parse_intent", {}, "empty params"))
    methods.append(("ai.parse_intent", {"text": ""}, "empty text"))
    methods.append(("ai.parse_intent", {"text": "hello"}, "simple text"))
    methods.append(("ai.parse_intent", None, "null params"))

    # --- translate.text ---
    methods.append(("translate.text", {}, "empty params"))
    methods.append(("translate.text", {"text": "hello"}, "simple text, no lang"))
    methods.append(("translate.text", {"text": "", "target_lang": "zh"}, "empty text"))
    methods.append(("translate.text", {"text": "hello", "target_lang": "zz"}, "bad lang code"))

    # --- translate.full ---
    methods.append(("translate.full", {}, "empty params"))
    methods.append(("translate.full", {"text": "hello"}, "text only, no langs"))
    methods.append(("translate.full", {"text": "hello", "target_lang": "zz"}, "bad lang"))
    methods.append(("translate.full", {"text": "", "target_lang": "zh"}, "empty text"))

    # --- translate.languages ---
    methods.append(("translate.languages", {}, "empty params"))

    # --- translate.word_detail ---
    methods.append(("translate.word_detail", {}, "empty params"))
    methods.append(("translate.word_detail", {"word": ""}, "empty word"))
    methods.append(("translate.word_detail", {"word": "hello"}, "valid word"))

    # --- aggregations ---
    methods.append(("aggregation.stats", {}, "empty params"))
    methods.append(("aggregation.logs", {}, "empty params"))
    methods.append(("aggregation.config", {}, "empty params"))

    # --- usage ---
    methods.append(("usage.stats", {}, "empty params"))
    methods.append(("usage.records", {}, "empty params"))
    methods.append(("usage.records", {"limit": "abc"}, "bad limit type"))
    methods.append(("usage.budget.get", {}, "empty params"))
    methods.append(("usage.budget.set", {}, "empty params"))
    methods.append(("usage.budget.set", {"budget": "not_a_number"}, "bad budget"))
    methods.append(("usage.check_budget", {}, "empty params"))
    methods.append(("usage.dashboard", {}, "empty params"))
    methods.append(("usage.today", {}, "empty params"))
    methods.append(("usage.export", {}, "empty params"))

    # --- chat ---
    methods.append(("chat.history", {}, "empty params"))
    methods.append(("chat.history", {"conversation_id": 9999999}, "bad conv id"))
    methods.append(("chat.list_conversations", {}, "empty params"))
    methods.append(("chat.get_conversation", {}, "empty params"))
    methods.append(("chat.get_conversation", {"conversation_id": 9999999}, "bad conv id"))

    # --- memory ---
    methods.append(("memory.search", {}, "empty params"))
    methods.append(("memory.search", {"query": "test"}, "with query"))
    methods.append(("memory.recent", {}, "empty params"))
    methods.append(("memory.related", {}, "empty params"))
    methods.append(("memory.related_text", {}, "empty params"))
    methods.append(("tags.list", {}, "empty params"))

    # --- daily ---
    methods.append(("daily.get", {}, "empty params"))
    methods.append(("daily.append", {}, "empty params"))
    methods.append(("daily.append", {"content": "test entry"}, "with content"))

    # --- file ---
    methods.append(("file.read", {}, "empty params"))
    methods.append(("file.write", {}, "empty params"))
    methods.append(("file.find", {}, "empty params"))
    methods.append(("file.find", {"pattern": "*.txt"}, "with pattern"))

    # --- siyi.ping ---
    methods.append(("siyi.ping", {}, "empty params"))

    # --- inbox ---
    methods.append(("inbox.list", {}, "empty params"))
    methods.append(("inbox.add", {}, "empty params"))
    methods.append(("inbox.add", {"content": "test item"}, "with content"))
    methods.append(("inbox.update", {}, "empty params"))
    methods.append(("inbox.classify", {}, "empty params"))
    methods.append(("inbox.relay", {}, "empty params"))
    methods.append(("inbox.list_confirmations", {}, "empty params"))

    # --- web ---
    methods.append(("web.search", {}, "empty params"))
    methods.append(("web.fetch", {}, "empty params"))

    # --- knowledge ---
    methods.append(("knowledge.search", {}, "empty params"))
    methods.append(("knowledge.relations", {}, "empty params"))
    methods.append(("knowledge.graph", {}, "empty params"))
    methods.append(("knowledge.organize", {}, "empty params"))

    # --- tasks ---
    methods.append(("tasks.list", {}, "empty params"))
    methods.append(("tasks.get", {}, "empty params"))
    methods.append(("tasks.get", {"task_id": 9999999}, "bad task id"))
    methods.append(("tasks.toggle", {}, "empty params"))
    methods.append(("tasks.toggle", {"task_id": 9999999}, "bad task id"))
    methods.append(("tasks.delete", {}, "empty params"))

    # --- notes ---
    methods.append(("notes.list", {}, "empty params"))
    methods.append(("notes.auto_tag", {}, "empty params"))
    methods.append(("notes.auto_tag", {"content": "test"}, "with content"))

    # --- word ---
    methods.append(("wordbook.list", {}, "empty params"))
    methods.append(("word.list", {}, "empty params"))
    methods.append(("word.list", {"wordbook_id": 9999999}, "bad wordbook id"))
    methods.append(("word.count_due", {}, "empty params"))
    methods.append(("word.list_due", {}, "empty params"))
    methods.append(("word.review", {}, "empty params"))

    # --- agent tasks ---
    methods.append(("agent.list", {}, "empty params"))
    methods.append(("agent.history", {}, "empty params"))

    # --- evolution ---
    methods.append(("evolution.status", {}, "empty params"))
    methods.append(("evolution.last_briefing", {}, "empty params"))

    # --- settings ---
    methods.append(("settings.get", {}, "empty params"))
    methods.append(("settings.set", {}, "empty params"))

    # --- system ---
    methods.append(("system.status", {}, "empty params"))
    methods.append(("system.info", {}, "empty params"))
    methods.append(("system.health", {}, "empty params"))
    methods.append(("system.active_tasks", {}, "empty params"))

    # --- notifications ---
    methods.append(("notification.list", {}, "empty params"))
    methods.append(("notification.stats", {}, "empty params"))
    methods.append(("notification.preferences.get", {}, "empty params"))

    # --- weather ---
    methods.append(("weather.current", {}, "empty params"))

    # --- sync ---
    methods.append(("sync.status", {}, "empty params"))

    # --- reminder ---
    methods.append(("reminder.list", {}, "empty params"))

    # --- capability ---
    methods.append(("capability.list", {}, "empty params"))

    # --- ocr ---
    methods.append(("ocr.status", {}, "empty params"))

    # --- reading ---
    methods.append(("reading.list", {}, "empty params"))
    methods.append(("reading.list_articles", {}, "empty params"))

    # --- sandbox ---
    methods.append(("sandbox.list_pending", {}, "empty params"))

    # --- provider ---
    methods.append(("provider.list", {}, "empty params"))
    methods.append(("provider.health", {}, "empty params"))
    methods.append(("provider.routing.get", {}, "empty params"))
    methods.append(("provider.routing.stats", {}, "empty params"))

    # --- kiro ---
    methods.append(("kiro.status", {}, "empty params"))

    # --- dictation ---
    methods.append(("word.dictation_check", {}, "empty params"))
    methods.append(("word.dictation_check", {"word": "hello", "attempt": "hello"}, "valid attempt"))

    # --- transfer ---
    methods.append(("transfer.token_info", {}, "empty params"))
    methods.append(("transfer.history", {}, "empty params"))
    methods.append(("transfer.files", {}, "empty params"))

    # --- narrative ---
    methods.append(("memory.narrative.latest", {}, "empty params"))
    methods.append(("memory.narrative.types", {}, "empty params"))
    methods.append(("agent.state.summary", {}, "empty params"))

    # --- api_keys ---
    methods.append(("api_keys.list", {}, "empty params"))
    methods.append(("api_keys.rotation.history", {}, "empty params"))

    # --- info proxy ---
    methods.append(("info.auto_config", {}, "empty params"))
    methods.append(("info.brief", {}, "empty params"))
    methods.append(("info.cached_items", {}, "empty params"))
    methods.append(("info.sections.list", {}, "empty params"))
    methods.append(("info.get_section_keywords", {}, "empty params"))

    # --- report ---
    methods.append(("report.daily_summary", {}, "empty params"))

    # --- remote (V2.1 批次1#7: remote.screenshot/remote.shell 已移除) ---
    methods.append(("remote.agent.execute", {}, "empty params"))

    # --- nonexistent method ---
    methods.append(("nonexistent.method", {}, "method should not exist"))

    # --- Invalid JSON-RPC: missing required fields ---
    global REQUEST_ID
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(WS_URL, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as ws:
                REQUEST_ID += 1
                await ws.send_json({"jsonrpc": "2.0", "id": REQUEST_ID})  # no method
                msg = await asyncio.wait_for(ws.receive_json(), timeout=TIMEOUT)
                results.append(("(no method)", {}, "missing method field", msg, None))
    except asyncio.TimeoutError:
        results.append(("(no method)", {}, "missing method field", None, "TIMEOUT"))
    except Exception as e:
        results.append(("(no method)", {}, "missing method field", None, f"CLIENT_ERROR: {e}"))

    # --- Run all method tests ---
    for method, params, desc in methods:
        m, p, d, r, err = await call(method, params, desc)
        results.append((m, p, d, r, err))
        sys.stdout.write(".")
        sys.stdout.flush()

    print()
    print("=" * 80)
    print("SMOKE RESULTS")
    print("=" * 80)

    issues_found = []
    total = len(results)

    for method, params, desc, response, error in results:
        if error:
            # TIMEOUT or client error
            sev = "HIGH" if "TIMEOUT" in str(error) else "MEDIUM"
            print(f"[{sev}] {method} ({desc}) -> {error}")
            issues_found.append(
                {
                    "severity": sev,
                    "method": method,
                    "desc": desc,
                    "issue": error,
                    "response": None,
                }
            )
        elif response is None:
            print(f"[HIGH] {method} ({desc}) -> no response (None)")
            issues_found.append(
                {
                    "severity": "HIGH",
                    "method": method,
                    "desc": desc,
                    "issue": "No response received",
                    "response": None,
                }
            )
        elif "error" in response:
            err_obj = response.get("error", {})
            err_code = err_obj.get("code", "?")
            err_msg = err_obj.get("message", "?")
            if err_code == -32603:
                # Internal error — unhandled exception
                data = err_obj.get("data", "")
                print(f"[CRITICAL] {method} ({desc}) -> -32603 Internal error: {err_msg}")
                if data:
                    print(f"    data: {str(data)[:300]}")
                issues_found.append(
                    {
                        "severity": "CRITICAL",
                        "method": method,
                        "desc": desc,
                        "issue": f"-32603 Internal error: {err_msg}",
                        "response": response,
                    }
                )
            elif err_code == -32601:
                # Method not found — expected for nonexistent
                if method == "nonexistent.method":
                    print(f"[OK] {method} ({desc}) -> correctly -32601 Method not found")
                    continue
                print(f"[MEDIUM] {method} ({desc}) -> -32601 Method not found (unexpected)")
                issues_found.append(
                    {
                        "severity": "MEDIUM",
                        "method": method,
                        "desc": desc,
                        "issue": "-32601 Method not found (unexpected)",
                        "response": response,
                    }
                )
            elif err_code == -32602:
                # Invalid params — expected for empty/errored params, but check for missing validation
                print(f"[INFO] {method} ({desc}) -> -32602 Invalid params: {err_msg}")
            elif err_code == -32000:
                print(f"[INFO] {method} ({desc}) -> -32000 App error: {err_msg}")
            else:
                print(f"[MEDIUM] {method} ({desc}) -> error code={err_code}: {err_msg}")
                issues_found.append(
                    {
                        "severity": "MEDIUM",
                        "method": method,
                        "desc": desc,
                        "issue": f"Unexpected error code={err_code}: {err_msg}",
                        "response": response,
                    }
                )
        elif "result" in response:
            # Success
            res = response.get("result", "")
            res_str = str(res)[:100]
            print(f"[OK] {method} ({desc}) -> result: {res_str}")

    print()
    print(f"Total: {total}, Issues: {len(issues_found)}")
    print()
    print("=== ISSUES ===")
    for i, iss in enumerate(issues_found):
        print(f"{i + 1}. [{iss['severity']}] {iss['method']} ({iss['desc']}) - {iss['issue']}")

    # Output JSON for parent agent consumption
    print()
    print("=== JSON_SUMMARY ===")
    print(json.dumps(issues_found, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
