# -*- coding: utf-8 -*-
"""P-I 长尾模块扫描探针（任务 #16）。

方法论：模拟客户 + 前后端对表，覆盖 15 个长尾命名空间。
每命名空间三件套：
  ① 读面全方法逐个调用（正确参数 + 故意错参名，验证返回是否诚实）
  ② 最小写旅程 + 读回 + 清理
  ③ 前端入口对表（grep 结果在报告里，本文件不重复）

隔离：SIYI_INTEGRATION_AUTOSTART=1 自启临时后端（随机端口 + 临时 data +
SIYI_TEST_OFFLINE=1），不碰 18765 / 生产 data。R4 方法走 sandbox.approve。

本探针以"记录行为"为主（降级/崩溃/诚实度），不强断言网络/AI 成功，
最终把矩阵 dump 到 D:/h/3.25/pi-longtail-matrix.json 供报告消费。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_MATRIX_PATH = os.environ.get("PI_MATRIX_PATH", "D:/h/3.25/pi-longtail-matrix.json")

# 探针结果累加器：{ "namespace::method": {...} }
_RESULTS: dict[str, dict[str, Any]] = {}


def _record(ns: str, method: str, kind: str, verdict: str, detail: str = "") -> None:
    key = f"{ns}::{method}"
    slot = _RESULTS.setdefault(key, {"ns": ns, "method": method, "calls": {}})
    slot["calls"][kind] = {"verdict": verdict, "detail": detail[:300]}


def _classify(resp: dict[str, Any]) -> tuple[str, str]:
    """把 rpc 响应归一为 verdict + detail。

    verdict ∈ {OK, DEGRADED, RPCERR, PENDING, TYPEERR}
    - RPCERR: JSON-RPC 层错误（-32602/-32603 等），handler 抛异常或未注册
    - DEGRADED: 正常返回但 result.success is False / 带 error 字段（业务降级，非崩溃）
    - PENDING: R4 审批挂起
    - OK: 正常成功
    """
    if "error" in resp and isinstance(resp.get("error"), dict):
        err = resp["error"]
        return "RPCERR", f"code={err.get('code')} msg={err.get('message')}"
    result = resp.get("result")
    if isinstance(result, dict):
        if result.get("pending"):
            return "PENDING", f"pending_id={result.get('pending_id')}"
        if result.get("success") is False:
            return "DEGRADED", str(result.get("error") or result.get("message") or result)[:200]
        if "error" in result and result.get("error"):
            return "DEGRADED", str(result.get("error"))[:200]
        return "OK", json.dumps(result, ensure_ascii=False)[:200]
    if isinstance(result, list):
        return "OK", f"list[{len(result)}]"
    return "OK", str(result)[:200]


async def _call(rpc, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return await rpc(method, params or {})


async def _probe(rpc, ns: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """跑一次并记录 verdict（正确参数用 kind='good'）。返回原始响应供写旅程继续。"""
    resp = await _call(rpc, method, params)
    v, d = _classify(resp)
    _record(ns, method, "good", v, d)
    return resp


async def _probe_bad(rpc, ns: str, method: str, bad_params: dict[str, Any]) -> None:
    """故意错参名：已知 **kwargs 吞参病灶——错参不报错而是换语义/走默认。
    记录 verdict，重点看它是不是"诚实拒绝"（应为 RPCERR/DEGRADED）还是"静默吞掉走默认"(OK)。"""
    resp = await _call(rpc, method, bad_params)
    v, d = _classify(resp)
    _record(ns, method, "bad", v, d)


# ────────────────────────────────────────────────────────────────
# 1. weather — 读面（离线应降级，不崩）
# ────────────────────────────────────────────────────────────────
class TestWeather:
    async def test_weather_read(self, rpc):
        r = await _probe(rpc, "weather", "weather.current", {"city": "beijing"})
        _record("weather", "weather.current", "empty", *_classify(await _call(rpc, "weather.current", {})))
        await _probe_bad(rpc, "weather", "weather.current", {"citx": "x"})  # 错参名→应吞参走默认城市(不诚实)


# ────────────────────────────────────────────────────────────────
# 2. reminder — 最小写旅程 create→list→delete(R4)
# ────────────────────────────────────────────────────────────────
class TestReminder:
    async def test_reminder_journey(self, rpc, rpc_approved):
        # create 需 reminder_time <= now 才会被 list 命中（list 过滤 <=now 且未 done）
        past = int((datetime.now() - timedelta(minutes=5)).timestamp() * 1000)
        r = await _probe(rpc, "reminder", "reminder.create", {"title": "__pi_probe_reminder__", "reminder_time": past})
        res = r.get("result", {})
        rid = res.get("id") if isinstance(res, dict) else None
        _record("reminder", "reminder.create", "detail", "OK" if rid else "NO_ID", json.dumps(res, ensure_ascii=False)[:200])
        # list 读回
        lr = await _call(rpc, "reminder.list", {})
        items = lr.get("result", [])
        hit = any((isinstance(x, dict) and x.get("id") == rid) for x in items) if isinstance(items, list) else False
        _record("reminder", "reminder.list", "good", "OK" if hit else "MISS", f"found={hit} n={len(items) if isinstance(items, list) else '?'}")
        # delete（R4→approve）清理
        if rid:
            dr = await rpc_approved("reminder.delete", {"id": rid})
            _record("reminder", "reminder.delete", "good", *_classify(dr))
        await _probe_bad(rpc, "reminder", "reminder.list", {"xyz": 1})
        await _probe_bad(rpc, "reminder", "reminder.create", {"titel": "bad"})  # 错参名→空标题


# ────────────────────────────────────────────────────────────────
# 3. rss — list(读) / subscribe+test+fetch(离线) / unsubscribe
# ────────────────────────────────────────────────────────────────
class TestRss:
    async def test_rss_read(self, rpc):
        await _probe(rpc, "rss", "rss.list", {})
        # 离线：test/subscribe 一个非法 URL，看降级非崩
        await _probe(rpc, "rss", "rss.test", {"url": "http://127.0.0.1:1/nope.xml"})
        await _probe(rpc, "rss", "rss.fetch_now", {})
        await _probe(rpc, "rss", "rss.unsubscribe", {"feed_id": "nonexistent"})
        await _probe_bad(rpc, "rss", "rss.subscribe", {"urz": "x"})  # 错参名→url 缺省→诚实报"缺少 url"


# ────────────────────────────────────────────────────────────────
# 4. skin — list/active(读) + activate→读回→还原
# ────────────────────────────────────────────────────────────────
class TestSkin:
    async def test_skin_journey(self, rpc):
        before = await _call(rpc, "skin.active", {})
        _record("skin", "skin.active", "good", *_classify(before))
        lr = await _probe(rpc, "skin", "skin.list", {})
        # 取一个 skin_id 激活→读回
        skins = (lr.get("result") or {}).get("skins") if isinstance(lr.get("result"), dict) else None
        sid = None
        if isinstance(skins, list) and skins:
            sid = skins[0].get("id")
        if sid:
            ar = await _probe(rpc, "skin", "skin.activate", {"skin_id": sid})
            # 读回验证
            chk = await _call(rpc, "skin.active", {})
            active_id = (chk.get("result") or {}).get("skin", {}).get("id") if isinstance(chk.get("result"), dict) else None
            _record("skin", "skin.activate", "detail", "OK" if active_id == sid else "MISMATCH", f"wanted={sid} got={active_id}")
        await _probe_bad(rpc, "skin", "skin.activate", {"skin_idx": "deep_ocean"})  # 错参名→skin_id 缺省


# ────────────────────────────────────────────────────────────────
# 5. personal — 读面全方法 + profile.update 写读还原 + set_sensitivity
# ────────────────────────────────────────────────────────────────
class TestPersonal:
    async def test_personal_read(self, rpc):
        await _probe(rpc, "personal", "personal.profile.get", {})
        await _probe(rpc, "personal", "personal.raw_items.stats", {})
        await _probe(rpc, "personal", "personal.raw_items.list", {"limit": 5})
        # review 同步直调（离线 AI 降级为纯统计）
        await _probe(rpc, "personal", "personal.review", {"period": "week"})

    async def test_personal_write(self, rpc):
        # 读当前画像 → update focus_topics → 读回 → 还原
        before = await _call(rpc, "personal.profile.get", {})
        prof = (before.get("result") or {}).get("profile") if isinstance(before.get("result"), dict) else None
        r = await _probe(rpc, "personal", "personal.profile.update", {"focus_topics": ["__pi_probe__"]})
        after_prof = (r.get("result") or {}).get("profile") if isinstance(r.get("result"), dict) else None
        got = None
        if isinstance(after_prof, dict):
            ft = (
                after_prof.get("settings", {}).get("focus_topics") if isinstance(after_prof.get("settings"), dict) else after_prof.get("focus_topics")
            )
            got = ft
        _record(
            "personal",
            "personal.profile.update",
            "detail",
            "OK" if got and "__pi_probe__" in json.dumps(got, ensure_ascii=False) else "CHECK",
            json.dumps(got, ensure_ascii=False)[:150] if got else str(after_prof)[:150],
        )
        # 还原
        await _call(rpc, "personal.profile.update", {"focus_topics": ["__pi_probe__"], "current_focus": ""})
        # extract（扫描 raw items，离线不崩即可）
        await _probe(rpc, "personal", "personal.profile.extract", {"limit": 5})
        await _probe(rpc, "personal", "personal.raw_items.set_sensitivity", {"item_id": "nonexistent", "sensitivity": "personal"})
        await _probe_bad(rpc, "personal", "personal.raw_items.set_sensitivity", {"item_idx": "x", "sens": "personal"})  # 错参名→诚实报必填
        await _probe_bad(rpc, "personal", "personal.review", {"periodd": "month"})  # 错参名→默认 week


# ────────────────────────────────────────────────────────────────
# 6. notification — 读面 + test(写一条) + list 命中 + delete(R4) 清理 + preferences
# ────────────────────────────────────────────────────────────────
class TestNotification:
    async def test_notif_read(self, rpc):
        await _probe(rpc, "notification", "notification.list", {"limit": 10})
        await _probe(rpc, "notification", "notification.stats", {})
        await _probe(rpc, "notification", "notification.preferences.get", {})

    async def test_notif_journey(self, rpc, rpc_approved):
        # test_channel(websocket) 会真实落一条通知 → list 命中 → delete
        tr = await _probe(rpc, "notification", "notification.test", {"channel": "websocket", "event_type": "__pi_probe__"})
        nid = None
        res = tr.get("result", {})
        if isinstance(res, dict) and res.get("ok") and isinstance(res.get("notification"), dict):
            nid = res["notification"].get("id")
        await asyncio.sleep(0.5)
        lr = await _call(rpc, "notification.list", {"limit": 50})
        items = lr.get("result", {}).get("items") if isinstance(lr.get("result"), dict) else None
        hit = any(x.get("id") == nid for x in items) if isinstance(items, list) and nid else False
        _record(
            "notification",
            "notification.list",
            "detail",
            "OK" if hit else "CHECK",
            f"wrote_id={nid} hit={hit} n={len(items) if isinstance(items, list) else '?'}",
        )
        if nid:
            await _probe(rpc, "notification", "notification.get", {"notification_id": nid})
            await _probe(rpc, "notification", "notification.mark_read", {"notification_id": nid, "read": True})
            # delete 是 R4
            dr = await rpc_approved("notification.delete", {"notification_id": nid})
            _record("notification", "notification.delete", "detail", *_classify(dr))
        await _probe(rpc, "notification", "notification.mark_all_read", {})
        # preferences.set 写回（幂等，不动 enabled）
        await _probe(rpc, "notification", "notification.preferences.set", {"subscribed_events": ["cron.completed"]})
        await _probe_bad(rpc, "notification", "notification.get", {"notification_idd": "x"})  # 错参名→诚实报 required
        await _probe_bad(rpc, "notification", "notification.list", {"limit": "abc"})  # 非整 limit→N-E 棘轮应 -32602


# ────────────────────────────────────────────────────────────────
# 7. process — 纯读面（无被追踪进程时 list 空）
# ────────────────────────────────────────────────────────────────
class TestProcess:
    async def test_process_read(self, rpc):
        await _probe(rpc, "process", "process.list", {"alive_only": False})
        await _probe(rpc, "process", "process.status", {"process_id": "nonexistent"})
        await _probe(rpc, "process", "process.output", {"process_id": "nonexistent", "stream": "stdout", "limit": 5})
        await _probe_bad(rpc, "process", "process.output", {"process_id": "nonexistent", "stream": "bogus"})  # 非法 stream→诚实报错
        await _probe_bad(rpc, "process", "process.list", {"alive_onlyy": True})


# ────────────────────────────────────────────────────────────────
# 8. web — 离线读面（search/fetch 应降级/超时非崩）
# ────────────────────────────────────────────────────────────────
class TestWeb:
    async def test_web_read(self, rpc):
        await _probe(rpc, "web", "web.search", {"query": "__pi_probe__"})  # 离线可能 error
        await _probe(rpc, "web", "web.fetch", {"url": "http://127.0.0.1:1/x"})
        await _probe_bad(rpc, "web", "web.search", {"queryy": "x"})  # 错参名→query 空→诚实报 required


# ────────────────────────────────────────────────────────────────
# 9. file — 写越界必拦 + 沙箱根内写读 + 清理
# ────────────────────────────────────────────────────────────────
class TestFile:
    async def test_file_read_only(self, rpc):
        await _probe(rpc, "file", "file.read", {"path": "nonexistent.txt"})
        await _probe(rpc, "file", "file.find", {"pattern": "*.md", "root": "."})
        # 越界写：绝对路径写到系统盘根，应被 path_outside_allowed_root 拦
        br = await _probe(rpc, "file", "file.write", {"path": "C:/Windows/__pi_probe__.txt", "content": "x"})
        res = br.get("result", {})
        # R2-B6（2026-09-17）：file.write 已纳入 _R4_METHODS —— 未审批调用返回**待审批信封**
        # {"pending": True, "requires_approval": True, "pending_id": ...}，这是"被正确拦住"
        # 的一种形态（等人工批准后才重放），必须与硬拒绝并列判定；
        # 只用 success/error/reason 三键会漏判 → 误报 WRITE_LEAK!!。
        pending = isinstance(res, dict) and (res.get("pending") is True or res.get("requires_approval") is True)
        blocked = isinstance(res, dict) and (pending or res.get("success") is False or "error" in res or res.get("reason"))
        _record(
            "file",
            "file.write",
            "detail",
            "BLOCKED(pending_approval)" if pending else ("BLOCKED" if blocked else "WRITE_LEAK!!"),
            json.dumps(res, ensure_ascii=False)[:200],
        )
        await _probe_bad(rpc, "file", "file.read", {"pathh": "x"})  # 错参名→path 空→诚实报 required


# ────────────────────────────────────────────────────────────────
# 10. capability / usage — 读面 + 写面（usage.record / capability.approve/dismiss）
# ────────────────────────────────────────────────────────────────
class TestCapability:
    async def test_capability_read(self, rpc):
        await _probe(rpc, "capability", "capability.list", {})
        await _probe(rpc, "capability", "usage.stats", {})

    async def test_capability_write(self, rpc):
        await _probe(rpc, "capability", "usage.record", {"action": "__pi_probe__", "domain": "test"})
        # approve 一个不存在的 cap_id → 诚实 not found
        await _probe(rpc, "capability", "capability.approve", {"cap_id": "nonexistent_cap"})
        await _probe(rpc, "capability", "capability.dismiss", {"cap_id": "nonexistent_cap"})
        await _probe_bad(rpc, "capability", "usage.record", {"actionn": "x"})  # 错参名→action 空→诚实 required


# ────────────────────────────────────────────────────────────────
# 11. ocr — status(读) + extract 退化路径（无 tesseract/文件应降级非崩）
# ────────────────────────────────────────────────────────────────
class TestOcr:
    async def test_ocr_read(self, rpc):
        await _probe(rpc, "ocr", "ocr.status", {})
        await _probe(rpc, "ocr", "ocr.extract_text", {"image_path": "/nonexistent.png"})
        await _probe(rpc, "ocr", "ocr.extract_base64", {"data": "aGVsbG8="})  # 非图片 base64→降级
        await _probe(rpc, "ocr", "ocr.extract_text", {"image_path": "/etc/passwd.txt"})  # 非图片后缀→拦
        await _probe_bad(rpc, "ocr", "ocr.extract_text", {"image_patth": "x"})


# ────────────────────────────────────────────────────────────────
# 12. dictation — word.dictation_check（纯计算，不依赖词库）
# ────────────────────────────────────────────────────────────────
class TestDictation:
    async def test_dictation(self, rpc):
        await _probe(rpc, "dictation", "word.dictation_check", {"expected": "hello", "actual": "helo", "word_id": 0})
        # expected 为空且 word_id 无效→诚实报错
        await _probe(rpc, "dictation", "word.dictation_check", {"actual": "x"})
        await _probe_bad(rpc, "dictation", "word.dictation_check", {"expecte": "hello"})  # 错参名→expected 空→诚实


# ────────────────────────────────────────────────────────────────
# 13. transcribe — status(读) + 退化路径（无 faster-whisper/文件降级非崩）
# ────────────────────────────────────────────────────────────────
class TestTranscribe:
    async def test_transcribe(self, rpc):
        await _probe(rpc, "transcribe", "transcribe.status", {})
        await _probe(rpc, "transcribe", "transcribe.transcribe_file", {"audio_path": "/nonexistent.wav"})
        await _probe(rpc, "transcribe", "transcribe.transcribe_base64", {"data": "aGVsbG8=", "format": "wav"})
        await _probe(rpc, "transcribe", "transcribe.transcribe_file", {"audio_path": "/x.pdf"})  # 非法后缀→拦
        await _probe_bad(rpc, "transcribe", "transcribe.transcribe_file", {"audio_patth": "x"})


# ────────────────────────────────────────────────────────────────
# 14. translate / translations / ai.parse_intent
# ────────────────────────────────────────────────────────────────
class TestTranslate:
    async def test_translate_read(self, rpc):
        await _probe(rpc, "translate", "translate.languages", {})
        # 离线 ai_client 可能 None/失败→降级（translated 空 + error），不崩
        await _probe(rpc, "translate", "translate.text", {"text": "hello world", "target": "zh"})
        await _probe(rpc, "translate", "translate.word_detail", {"word": "serendipity"})
        await _probe(rpc, "translate", "translations.list", {"limit": 5})

    async def test_translate_write(self, rpc):
        sr = await _probe(
            rpc,
            "translate",
            "translations.save",
            {"source_text": "__pi_probe__", "translated_text": "测试", "source_lang": "en", "target_lang": "zh"},
        )
        res = sr.get("result", {})
        new_id = res.get("id") if isinstance(res, dict) else None
        _record(
            "translate",
            "translations.save",
            "detail",
            "OK" if isinstance(res, dict) and res.get("success") else "CHECK",
            json.dumps(res, ensure_ascii=False)[:150],
        )
        # 读回
        lr = await _call(rpc, "translations.list", {"limit": 20})
        items = lr.get("result", [])
        hit = any("__pi_probe__" in json.dumps(x, ensure_ascii=False) for x in items) if isinstance(items, list) else False
        _record("translate", "translations.list", "detail", "OK" if hit else "MISS", f"hit={hit}")
        await _probe(rpc, "translate", "ai.parse_intent", {"text": "帮我记一下明天开会"})
        await _probe_bad(rpc, "translate", "translate.text", {"txt": "hello"})  # 错参名→text 空→短路返回空 translated（不诚实：静默空）


# ────────────────────────────────────────────────────────────────
# 15. url_learning — 离线读面 + file_path 白名单守卫
# ────────────────────────────────────────────────────────────────
class TestUrlLearning:
    async def test_url_learning(self, rpc):
        # 白名单外绝对路径应被拒（防越权读取）
        br = await _probe(rpc, "url_learning", "url_learning.learn", {"file_path": "C:/Windows/win.ini"})
        res = br.get("result", {})
        blocked = isinstance(res, dict) and res.get("error") and "白名单" in str(res.get("error"))
        _record("url_learning", "url_learning.learn", "detail", "BLOCKED" if blocked else "CHECK", json.dumps(res, ensure_ascii=False)[:200])
        await _probe(rpc, "url_learning", "url_learning.learn", {"url": "http://127.0.0.1:1/x"})  # 离线降级
        await _probe_bad(rpc, "url_learning", "url_learning.learn", {"urls": "x"})  # 错参名→url+file_path 均空→诚实 required
        await _probe(rpc, "url_learning", "url_learning.learn", {})  # 两参均空→诚实


# ────────────────────────────────────────────────────────────────
# 汇总 dump
# ────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session", autouse=True)
def _dump_matrix():
    yield
    Path(_MATRIX_PATH).write_text(json.dumps(_RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
