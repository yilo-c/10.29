# -*- coding: utf-8 -*-
"""P-G 功能模块客户旅程探针：求职猎手 + 信息（2026-09-17）。

方法论：模拟客户完成一件事后**写后读**断言，监控降级信封是否对用户诚实，
而非测"方法返回 200"。全程只进隔离临时后端（SIYI_INTEGRATION_AUTOSTART=1，
conftest 随机端口 + 临时 data + SIYI_TEST_OFFLINE=1）。零生产写入。

产出台账文件：SIYI_PG_LEDGER 指定路径（默认仓库根外临时），供报告引用。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_LEDGER_PATH = os.environ.get(
    "SIYI_PG_LEDGER",
    os.path.join(os.environ.get("TEMP", "/tmp"), "pg_jobinfo_ledger.json"),
)
_ledger: list[dict[str, Any]] = []


def _rec(step: str, method: str, verdict: str, detail: Any) -> None:
    _ledger.append({"step": step, "method": method, "verdict": verdict, "detail": detail})
    try:
        with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump(_ledger, f, ensure_ascii=False, indent=2, default=str)
    except Exception:  # noqa: BLE001
        pass


async def _timed(rpc, method: str, params: dict | None = None):
    t0 = time.monotonic()
    resp = await rpc(method, params or {})
    dt = round(time.monotonic() - t0, 3)
    return resp, dt


def _result(resp: dict) -> Any:
    return resp.get("result") if isinstance(resp, dict) else None


def _err(resp: dict) -> Any:
    return resp.get("error") if isinstance(resp, dict) else None


RESUME_TEXT = (
    "张伟，男，28岁，坐标深圳。本科计算机科学与技术，5年后端开发经验。"
    "精通 Python、Go，熟悉 FastAPI、Django、Flask Web 框架；熟练使用 MySQL、Redis、"
    "PostgreSQL，了解 MongoDB。有分布式系统、微服务架构设计经验，主导过日活百万级订单系统重构。"
    "熟悉 Docker、Kubernetes、消息队列 Kafka、RabbitMQ。具备 CI/CD、Git、Linux 运维经验。"
    "英语 CET-6，能快速阅读英文技术文档。期望岗位：高级后端工程师 / 架构师。期望薪资 40-55K。"
    "参与过开源项目贡献，GitHub 有个人作品。擅长性能优化与高并发场景问题排查。"
)


async def test_pg0_registry_snapshot_vs_static(rpc):
    """① 前置对账：运行时注册表快照 vs 静态 385 清单（重点 job_hunter/info 域）。"""
    resp, dt = await _timed(rpc, "system.status", {})
    methods = _result(resp) or {}
    names = methods.get("methods") if isinstance(methods, dict) else None
    assert isinstance(names, list) and len(names) > 100, f"system.status.methods 异常: {names}"
    jh = sorted(m for m in names if m.startswith("job_hunter."))
    info = sorted(m for m in names if m.startswith("info."))
    _rec(
        "registry_snapshot",
        "system.status",
        "ok",
        {
            "total": len(names),
            "job_hunter_count": len(jh),
            "info_count": len(info),
            "job_hunter": jh,
            "info": info,
            "elapsed": dt,
        },
    )


async def test_pg1_job_hunter_journey(rpc):
    """② 求职猎手客户旅程：profile→上传→extract→diagnose→rewrite→match→analyze→读回。"""
    # --- profile.set 写 ---
    resp, dt = await _timed(
        rpc,
        "job_hunter.profile.set",
        {
            "profile": {
                "name": "张伟",
                "city": "深圳",
                "years_of_experience": 5,
                "tech_stack": ["Python", "Go", "FastAPI", "Kubernetes"],
                "target_positions": ["高级后端工程师", "架构师"],
                "expected_salary": "40-55K",
                "education": "本科",
                "exclude_keywords": ["外包", "驻场"],
            }
        },
    )
    r = _result(resp)
    _rec(
        "1_profile.set",
        "job_hunter.profile.set",
        "pass" if isinstance(r, dict) and r.get("success") else "fail",
        {"resp": r or _err(resp), "elapsed": dt},
    )
    # --- 写后读：profile.get 应反映 set 内容 ---
    resp, dt = await _timed(rpc, "job_hunter.profile.get", {})
    r = _result(resp) or {}
    prof = r.get("profile") or {}
    readback_ok = prof.get("name") == "张伟" and "Python" in (prof.get("tech_stack") or [])
    _rec(
        "1b_profile.get_readback",
        "job_hunter.profile.get",
        "pass" if readback_ok else "write_lost",
        {"name": prof.get("name"), "tech_stack": prof.get("tech_stack"), "success_flag": r.get("success"), "elapsed": dt},
    )

    # --- resume.upload_and_parse（txt，无 AI，纯解析）---
    b64 = base64.b64encode(RESUME_TEXT.encode("utf-8")).decode("ascii")
    resp, dt = await _timed(rpc, "job_hunter.resume.upload_and_parse", {"filename": "resume.txt", "file_data": b64})
    r = _result(resp)
    parsed_ok = isinstance(r, dict) and r.get("success") and (r.get("text") or "").find("Python") >= 0
    _rec(
        "2_resume.upload_and_parse",
        "job_hunter.resume.upload_and_parse",
        "pass" if parsed_ok else "fail",
        {
            "success": (r or {}).get("success") if isinstance(r, dict) else r,
            "text_len": len((r or {}).get("text", "")) if isinstance(r, dict) else 0,
            "elapsed": dt,
        },
    )
    # 缺参漂移检查
    resp, dt = await _timed(rpc, "job_hunter.resume.upload_and_parse", {"filename": "x.txt"})
    r = _result(resp)
    _rec(
        "2b_upload_missing_file_data",
        "job_hunter.resume.upload_and_parse",
        "honest_missing_param" if isinstance(r, dict) and not r.get("success") else "review",
        {"resp": r or _err(resp), "elapsed": dt},
    )

    # --- profile.extract_from_resume（需 AI；无 key 环境看降级信封）---
    resp, dt = await _timed(rpc, "job_hunter.profile.extract_from_resume", {"resume_text": RESUME_TEXT})
    r = _result(resp)
    _rec(
        "3_extract_from_resume",
        "job_hunter.profile.extract_from_resume",
        _classify_ai_degrade(resp, dt),
        {"result_shape": _shape(r), "error": _err(resp), "elapsed": dt},
    )

    # --- resume.diagnose（需 AI）---
    resp, dt = await _timed(rpc, "job_hunter.resume.diagnose", {"resume_text": RESUME_TEXT, "target_role": "高级后端工程师", "industry": "互联网"})
    _rec(
        "4_resume.diagnose",
        "job_hunter.resume.diagnose",
        _classify_ai_degrade(resp, dt),
        {"result_shape": _shape(_result(resp)), "error": _err(resp), "elapsed": dt},
    )

    # --- resume.rewrite（需 AI）---
    resp, dt = await _timed(rpc, "job_hunter.resume.rewrite", {"resume_text": RESUME_TEXT, "target_role": "架构师", "style": "professional"})
    _rec(
        "5_resume.rewrite",
        "job_hunter.resume.rewrite",
        _classify_ai_degrade(resp, dt),
        {"result_shape": _shape(_result(resp)), "error": _err(resp), "elapsed": dt},
    )

    # --- scrape.run（无网络：入队→worker 执行→scraper 失败）---
    resp, dt = await _timed(rpc, "job_hunter.scrape.run", {"platforms": ["boss"], "keywords": ["python"], "city": "深圳", "page": 1})
    r = _result(resp)
    job_id = (r or {}).get("job_id") if isinstance(r, dict) else None
    _rec(
        "6_scrape.run",
        "job_hunter.scrape.run",
        "queued" if isinstance(r, dict) and r.get("success") and job_id else "fail",
        {"resp": r or _err(resp), "job_id": job_id, "elapsed": dt},
    )

    # --- scrape.status 轮询抓取任务真实终态（异步执行链证据）---
    if job_id:
        final = None
        for _ in range(12):  # 最多 ~36s
            await asyncio.sleep(3)
            resp, dt = await _timed(rpc, "job_hunter.scrape.status", {"job_id": job_id})
            r = _result(resp) or {}
            st = r.get("status")
            if st in ("completed", "failed"):
                final = {"status": st, "task": r.get("task")}
                break
            final = {"status": st, "elapsed_last": dt}
        _rec(
            "7_scrape.status_poll",
            "job_hunter.scrape.status",
            "terminal" if final and final.get("status") in ("completed", "failed") else "timeout_or_stuck",
            {"final": final},
        )

    # --- jobs.list 读回（scrape 是否真写入库）---
    resp, dt = await _timed(rpc, "job_hunter.jobs.list", {"limit": 50, "offset": 0})
    r = _result(resp) or {}
    jobs = r.get("jobs") or []
    _rec(
        "8_jobs.list",
        "job_hunter.jobs.list",
        "pass" if isinstance(jobs, list) else "fail",
        {"count": r.get("count"), "len": len(jobs), "elapsed": dt},
    )

    # --- match（无 job 或无 AI）---
    resp, dt = await _timed(rpc, "job_hunter.match", {})
    _rec("9_match", "job_hunter.match", _classify_ai_degrade(resp, dt), {"result_shape": _shape(_result(resp)), "error": _err(resp), "elapsed": dt})

    # --- recommendations / stats 读回 ---
    resp, dt = await _timed(rpc, "job_hunter.recommendations", {"limit": 5})
    _rec(
        "10_recommendations",
        "job_hunter.recommendations",
        "pass" if isinstance(_result(resp), dict) else "fail",
        {"result_shape": _shape(_result(resp)), "elapsed": dt},
    )
    resp, dt = await _timed(rpc, "job_hunter.stats", {})
    _rec("11_stats", "job_hunter.stats", "pass" if isinstance(_result(resp), dict) else "fail", {"stats": _result(resp), "elapsed": dt})


def _shape(r: Any) -> Any:
    if isinstance(r, dict):
        return {k: (type(v).__name__ if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in list(r.items())[:12]}
    return type(r).__name__


def _classify_ai_degrade(resp: dict, dt: float) -> str:
    """无 AI key 环境：区分 干净降级信封 / JSON-RPC error / 超时 / 崩溃(null)。"""
    err = _err(resp)
    r = _result(resp)
    if err is not None:
        return "rpc_error"
    if r is None:
        return "null_result_suspect_crash"
    if isinstance(r, dict):
        blob = json.dumps(r, ensure_ascii=False).lower()
        if r.get("success") and (r.get("profile") or r.get("matched") is not None or r.get("jobs") or r.get("analysis")):
            return "pass_ai_ok"
        if "ai" in blob and ("不可用" in blob or "unavailable" in blob or "失败" in blob or "error" in blob):
            return "honest_ai_degrade"
        if r.get("success") is False and r.get("error"):
            return "honest_error_envelope"
    return "review"


async def test_pg2_orphan_rpc(rpc):
    """④ 孤儿四件套 jobs.get/fetch_detail/scrape.status(无参)/analyze 直接调用。"""
    resp, dt = await _timed(rpc, "job_hunter.jobs.get", {"job_id": "nonexistent-xyz"})
    _rec(
        "orphan_jobs.get_notfound",
        "job_hunter.jobs.get",
        "honest_error_envelope" if (_result(resp) or {}).get("success") is False else "review",
        {"resp": _result(resp) or _err(resp), "elapsed": dt},
    )
    resp, dt = await _timed(rpc, "job_hunter.jobs.get", {})
    _rec(
        "orphan_jobs.get_missing_id",
        "job_hunter.jobs.get",
        "honest_missing_param" if (_result(resp) or {}).get("success") is False or _err(resp) else "review",
        {"resp": _result(resp) or _err(resp), "elapsed": dt},
    )

    resp, dt = await _timed(rpc, "job_hunter.jobs.fetch_detail", {"job_id": "nonexistent-xyz"})
    _rec(
        "orphan_fetch_detail_notfound",
        "job_hunter.jobs.fetch_detail",
        "honest_error_envelope" if (_result(resp) or {}).get("success") is False else _classify_ai_degrade(resp, dt),
        {"resp": _result(resp) or _err(resp), "elapsed": dt},
    )

    resp, dt = await _timed(rpc, "job_hunter.analyze", {"job_id": "nonexistent-xyz"})
    _rec(
        "orphan_analyze_notfound",
        "job_hunter.analyze",
        "honest_error_envelope" if (_result(resp) or {}).get("success") is False else _classify_ai_degrade(resp, dt),
        {"resp": _result(resp) or _err(resp), "elapsed": dt},
    )

    # scrape.status 无参 = 返回 scheduler 配置（既有契约）
    resp, dt = await _timed(rpc, "job_hunter.scrape.status", {})
    r = _result(resp)
    _rec(
        "orphan_scrape.status_noarg",
        "job_hunter.scrape.status",
        "pass_scheduler_config" if isinstance(r, dict) and "scheduler" in r else "review",
        {"resp": r or _err(resp), "elapsed": dt},
    )


async def test_pg3_info_sources_closed_loop(rpc):
    """③ info.sources 闭环：add→list 读回→toggle→collect→items 读回→remove→list 确认。"""
    # 起本地 RSS HTTP 服务，绕开外网，验证 add→collect→persist→items.read 全链真写库
    from aiohttp import web

    RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>PG Test Feed</title>
<item><title>PG探针自定义源条目Alpha</title><link>https://pg.test/alpha</link><description>alpha summary python kubernetes</description><pubDate>Mon, 01 Sep 2025 00:00:00 GMT</pubDate></item>
<item><title>PG探针自定义源条目Beta</title><link>https://pg.test/beta</link><description>beta summary go backend</description><pubDate>Mon, 01 Sep 2025 01:00:00 GMT</pubDate></item>
</channel></rss>"""

    async def _rss_handler(request):
        return web.Response(text=RSS_XML, content_type="application/xml")

    app = web.Application()
    app.router.add_get("/feed", _rss_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    feed_url = f"http://127.0.0.1:{port}/feed"

    try:
        # --- add ---
        resp, dt = await _timed(
            rpc,
            "info.sources.add",
            {
                "name": "pg_test_feed",
                "url": feed_url,
                "parser_type": "rss",
                "language": "zh",
                "category_hint": "dev",
                "display_name": "PG探针源",
            },
        )
        r = _result(resp)
        add_ok = isinstance(r, dict) and r.get("success")
        _rec("info_sources.add", "info.sources.add", "pass" if add_ok else "fail", {"resp": r or _err(resp), "elapsed": dt})

        # --- 写后读：list 应含 pg_test_feed ---
        resp, dt = await _timed(rpc, "info.sources.list", {})
        r = _result(resp) or {}
        srcs = r.get("sources") or []
        found = next((s for s in srcs if s.get("name") == "pg_test_feed"), None)
        _rec(
            "info_sources.list_readback",
            "info.sources.list",
            "pass" if found else "write_lost",
            {
                "found": bool(found),
                "custom_flag": (found or {}).get("is_custom"),
                "enabled_flag": (found or {}).get("enabled"),
                "total": r.get("total"),
                "elapsed": dt,
            },
        )

        # --- toggle 禁用→读回→启用 ---
        resp, dt = await _timed(rpc, "info.sources.toggle", {"name": "pg_test_feed", "enabled": False})
        r = _result(resp)
        _rec(
            "info_sources.toggle_off",
            "info.sources.toggle",
            "pass" if isinstance(r, dict) and r.get("success") else "fail",
            {"resp": r or _err(resp), "elapsed": dt},
        )
        resp, _ = await _timed(rpc, "info.sources.list", {})
        srcs = (_result(resp) or {}).get("sources") or []
        off_state = next((s.get("enabled") for s in srcs if s.get("name") == "pg_test_feed"), None)
        _rec("info_sources.toggle_readback", "info.sources.list", "pass" if off_state is False else "write_lost", {"enabled_after_off": off_state})
        # 禁用态下 collect 该源应被跳过（不产 items）
        await _timed(rpc, "info.brief", {"sources": ["pg_test_feed"], "no_ai": True})
        # 再启用，供 collect 正路径
        await _timed(rpc, "info.sources.toggle", {"name": "pg_test_feed", "enabled": True})

        # --- collect：info.brief 限定自定义源 + no_ai（本地无外网，唯一可跑通真写库路径）---
        resp, dt = await _timed(rpc, "info.brief", {"sources": ["pg_test_feed"], "no_ai": True})
        r = _result(resp) or {}
        collected = r.get("items") if isinstance(r, dict) else None
        _rec(
            "info_brief_collect_custom",
            "info.brief",
            "pass" if isinstance(collected, list) and len(collected) >= 1 else "no_data",
            {"items_len": len(collected) if isinstance(collected, list) else None, "errors": r.get("errors"), "elapsed": dt},
        )

        # --- items.list 读回：PG 探针条目应落库 ---
        resp, dt = await _timed(rpc, "info.items.list", {"search": "PG探针", "limit": 50})
        r = _result(resp) or {}
        persisted = r.get("items") or []
        pg_titles = [it.get("title") for it in persisted if "PG探针" in str(it.get("title", ""))]
        _rec(
            "info_items.readback",
            "info.items.list",
            "pass_persisted" if len(pg_titles) >= 1 else "collect_not_persisted",
            {"pg_titles": pg_titles, "total": r.get("total"), "elapsed": dt},
        )

        # --- remove ---
        resp, dt = await _timed(rpc, "info.sources.remove", {"name": "pg_test_feed"})
        r = _result(resp)
        _rec(
            "info_sources.remove",
            "info.sources.remove",
            "pass" if isinstance(r, dict) and r.get("success") else "fail",
            {"resp": r or _err(resp), "elapsed": dt},
        )
        resp, _ = await _timed(rpc, "info.sources.list", {})
        srcs = (_result(resp) or {}).get("sources") or []
        gone = not any(s.get("name") == "pg_test_feed" for s in srcs)
        _rec("info_sources.remove_readback", "info.sources.list", "pass" if gone else "delete_lost", {"still_present": not gone})

        # --- reset（清自定义源 + 恢复默认启用）---
        resp, dt = await _timed(rpc, "info.sources.reset", {})
        r = _result(resp)
        _rec(
            "info_sources.reset",
            "info.sources.reset",
            "pass" if isinstance(r, dict) and r.get("success") else "fail",
            {"source_count": len((r or {}).get("sources", [])), "elapsed": dt},
        )
    finally:
        await runner.cleanup()


async def test_pg4_info_items_pagination(rpc):
    """③ info.items 分页复测：N-E 新契约下 limit=50/10000/越界 + offset 是否真生效。"""
    # 基准：limit=500（上限内）取全量 total
    resp, dt = await _timed(rpc, "info.items.list", {"limit": 500, "offset": 0})
    base = _result(resp) or {}
    total = base.get("total")
    base_items = base.get("items") or []
    _rec("items.limit500", "info.items.list", "pass", {"total": total, "returned": len(base_items), "elapsed": dt})

    # limit=50 边界
    resp, _ = await _timed(rpc, "info.items.list", {"limit": 50, "offset": 0})
    r50 = _result(resp) or {}
    _rec("items.limit50", "info.items.list", "pass" if len(r50.get("items", [])) <= 50 else "limit_ignored", {"returned": len(r50.get("items", []))})

    # offset 生效性：limit=1 时 offset=1 应返回与 offset=0 不同的首条（若 total>=2）
    first0 = _first_id((await rpc("info.items.list", {"limit": 1, "offset": 0}) or {}).get("result", {}).get("items", []))
    first1 = _first_id((await rpc("info.items.list", {"limit": 1, "offset": 1}) or {}).get("result", {}).get("items", []))
    if (total or 0) >= 2:
        _rec(
            "items.offset_effective",
            "info.items.list",
            "pass" if first0 != first1 else "BUG_offset_ignored",
            {"first0": first0, "first1": first1, "total": total},
        )
    else:
        _rec("items.offset_effective", "info.items.list", "skip_not_enough_rows", {"total": total, "first0": first0, "first1": first1})

    # limit=10000（N-E 上边界，应放行）
    resp, dt = await _timed(rpc, "info.items.list", {"limit": 10000})
    _rec("items.limit10000", "info.items.list", "pass" if _err(resp) is None else "rejected", {"error": _err(resp), "elapsed": dt})
    # limit=10001（越界 → N-E 应 -32602）
    resp, dt = await _timed(rpc, "info.items.list", {"limit": 10001})
    e = _err(resp)
    _rec(
        "items.limit10001_oob",
        "info.items.list",
        "pass_ratchet" if e and str(e.get("code")) == "-32602" else ("leaked" if e is None else "other_err"),
        {"error": e, "elapsed": dt},
    )
    # offset=-1（越界 → -32602）
    resp, _ = await _timed(rpc, "info.items.list", {"offset": -1})
    e = _err(resp)
    _rec(
        "items.offset_neg",
        "info.items.list",
        "pass_ratchet" if e and str(e.get("code")) == "-32602" else ("leaked" if e is None else "other_err"),
        {"error": e},
    )


def _first_id(items: list) -> Any:
    return items[0].get("id") if items and isinstance(items[0], dict) else None


async def test_pg5_info_proxy_personalize(rpc):
    """③ fetch_proxy / personalize / items.get 实测。"""
    # fetch_proxy 需外网（zhihu/weibo）——无网应诚实降级为空/err，不崩
    resp, dt = await _timed(rpc, "info.fetch_proxy", {"source": "zhihu"})
    r = _result(resp)
    _rec(
        "info.fetch_proxy_zhihu",
        "info.fetch_proxy",
        "empty_honest" if isinstance(r, dict) and r.get("items") == [] else "review",
        {"result_shape": _shape(r), "error": _err(resp), "elapsed": dt},
    )

    # personalize：喂固定 items，应返回 order + scores
    resp, dt = await _timed(
        rpc,
        "info.personalize",
        {
            "items": [
                {"title": "Python 3.13 发布", "url": "https://p/1", "snippet": "kubernetes 后端"},
                {"title": "无关内容", "url": "https://p/2", "snippet": "烹饪"},
            ]
        },
    )
    r = _result(resp) or {}
    _rec("info.personalize", "info.personalize", "pass" if "order" in r and "scores" in r else "fail", {"result_shape": _shape(r), "elapsed": dt})

    # items.get 缺 item_id → schema required 应 -32602
    resp, _ = await _timed(rpc, "info.items.get", {})
    e = _err(resp)
    _rec("items.get_missing_id", "info.items.get", "pass_schema_required" if e and str(e.get("code")) == "-32602" else "review", {"error": e})


async def test_pg6_info_sections_and_misc(rpc):
    """③ info.sections CRUD + 其余 info.* 读方法冒烟（诚实形态）。"""
    for m, p in [
        ("info.sections.list", {}),
        ("info.items.stats", {}),
        ("info.items.sources", {}),
        ("info.cached_items", {}),
        ("info.get_section_keywords", {}),
    ]:
        resp, dt = await _timed(rpc, m, p)
        r = _result(resp)
        _rec(
            f"info_misc::{m}",
            m,
            "pass" if (r is not None and _err(resp) is None) else "fail",
            {"result_shape": _shape(r), "error": _err(resp), "elapsed": dt},
        )


def test_pg99_dump_ledger():
    """收尾：把台账路径打印（供人工/报告读取）。"""
    with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(_ledger, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[PG-LEDGER] written to {_LEDGER_PATH} ({len(_ledger)} entries)")
