# -*- coding: utf-8 -*-
"""P-G 第二轮补全：剩余 job_hunter/info 方法 + R4 审批闭环 + items 写链。台账写 pg_ledger2.json。"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_LEDGER_PATH = os.environ.get("SIYI_PG_LEDGER2", os.path.join(os.environ.get("TEMP", "/tmp"), "pg_ledger2.json"))
_ledger: list[dict[str, Any]] = []


def _rec(step, method, verdict, detail):
    _ledger.append({"step": step, "method": method, "verdict": verdict, "detail": detail})
    try:
        with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump(_ledger, f, ensure_ascii=False, indent=2, default=str)
    except Exception:  # noqa: BLE001
        pass


def _result(resp):
    return resp.get("result") if isinstance(resp, dict) else None


def _err(resp):
    return resp.get("error") if isinstance(resp, dict) else None


async def _timed(rpc, m, p=None):
    import time

    t0 = time.monotonic()
    r = await rpc(m, p or {})
    return r, round(time.monotonic() - t0, 3)


async def test_pgA_job_hunter_remaining(rpc):
    """job_hunter 剩余方法：platforms/set_status/rewrite_section/cold_message/advice/interview_prep。"""
    resp, dt = await _timed(rpc, "job_hunter.platforms", {})
    r = _result(resp) or {}
    plats = r.get("platforms") or []
    _rec(
        "jh_platforms",
        "job_hunter.platforms",
        "pass" if len(plats) == 5 else "review",
        {"count": len(plats), "implemented_flags": {p["id"]: p.get("implemented") for p in plats}, "elapsed": dt},
    )

    resp, dt = await _timed(rpc, "job_hunter.jobs.set_status", {"job_id": "no-such", "status": "applied"})
    r = _result(resp) or {}
    _rec(
        "jh_set_status_notfound", "job_hunter.jobs.set_status", "honest_error" if r.get("success") is False else "review", {"resp": r, "elapsed": dt}
    )
    resp, _ = await _timed(rpc, "job_hunter.jobs.set_status", {"job_id": "no-such", "status": "bogus"})
    r = _result(resp) or {}
    _rec(
        "jh_set_status_invalid_enum",
        "job_hunter.jobs.set_status",
        "honest_error" if r.get("success") is False and "invalid status" in str(r.get("error", "")) else "review",
        {"resp": r},
    )

    resp, dt = await _timed(
        rpc, "job_hunter.resume.rewrite_section", {"resume_text": "技能：Python 五年经验，主导高并发系统设计。", "section": "专业技能"}
    )
    r = _result(resp) or {}
    _rec(
        "jh_rewrite_section_ai_degrade",
        "job_hunter.resume.rewrite_section",
        "honest_ai_degrade" if (r.get("success") is False and "AI" in str(r.get("error", ""))) else "review",
        {"resp_keys_shape": {k: type(v).__name__ for k, v in r.items()}, "elapsed": dt},
    )
    resp, _ = await _timed(rpc, "job_hunter.resume.rewrite_section", {"resume_text": "abc"})
    r = _result(resp) or {}
    _rec(
        "jh_rewrite_section_missing_section",
        "job_hunter.resume.rewrite_section",
        "honest_missing_param" if r.get("success") is False else "review",
        {"resp": r},
    )

    for m in ("job_hunter.cold_message.generate", "job_hunter.resume_advice", "job_hunter.interview_prep"):
        resp, dt = await _timed(rpc, m, {"job_id": "no-such"})
        r = _result(resp) or {}
        _rec(f"jh::{m}", m, "honest_not_found" if r.get("success") is False else "review", {"resp": r, "elapsed": dt})


async def test_pgB_analyze_stream(rpc):
    """analyze_stream 流式链路（无 job → 应得"职位不存在"；不崩、不断流）。"""
    chunks: list[str] = []
    try:
        resp = await rpc("job_hunter.analyze_stream", {"job_id": "no-such"})
        # D-PG-4 修复后（info-fix 2026-09-17）契约变更：非流式调用 asyncgen
        # handler 由 registry asyncgen 分支收集为 **chunk 列表**（与 chat.send
        # 同约定），不再是 str。双形态兼容断言。
        r = _result(resp)
        text = r if isinstance(r, str) else "".join(str(c) for c in (r or []))
        _rec(
            "jh_analyze_stream",
            "job_hunter.analyze_stream",
            "pass_stream" if "不存在" in text else "review",
            {"result_shape": type(r).__name__, "result": text[:200], "error": _err(resp)},
        )
    except asyncio.TimeoutError:
        _rec("jh_analyze_stream", "job_hunter.analyze_stream", "TIMEOUT", {"chunks_so_far": chunks})


async def test_pgC_r4_approval_closed_loop(rpc):
    """R4 审批闭环：sources.remove / reset 走 pending→approve→执行→读回。"""
    # 先确保源存在（add 非 R4，直通）
    await _timed(rpc, "info.sources.add", {"name": "pg_r4_feed", "url": "http://127.0.0.1:9/feed", "parser_type": "rss"})
    resp, dt = await _timed(rpc, "info.sources.remove", {"name": "pg_r4_feed"})
    r = _result(resp)
    if isinstance(r, dict) and r.get("pending"):
        pid = r.get("pending_id")
        ack, dt2 = await _timed(rpc, "sandbox.approve", {"pending_id": pid})
        ar = _result(ack)
        ok = isinstance(ar, dict) and (ar.get("success") or (ar.get("result") or {}).get("success"))
        _rec(
            "r4_remove_approve",
            "sandbox.approve",
            "pass" if ok else "approve_but_fail",
            {
                "ack_shape": {k: type(v).__name__ for k, v in (ar or {}).items()} if isinstance(ar, dict) else ar,
                "error": _err(ack),
                "elapsed": dt + dt2,
            },
        )
        resp, _ = await _timed(rpc, "info.sources.list", {})
        srcs = (_result(resp) or {}).get("sources") or []
        gone = not any(s.get("name") == "pg_r4_feed" for s in srcs)
        _rec("r4_remove_readback", "info.sources.list", "pass" if gone else "delete_lost", {"gone": gone})
    else:
        _rec("r4_remove_approve", "info.sources.remove", "not_pending_unexpected", {"resp": r, "elapsed": dt})

    # reset：清空 overrides（当前无自定义源时也应成功返回默认清单）
    resp, dt = await _timed(rpc, "info.sources.reset", {})
    r = _result(resp)
    if isinstance(r, dict) and r.get("pending"):
        ack, _ = await _timed(rpc, "sandbox.approve", {"pending_id": r["pending_id"]})
        r2 = _result(ack) or {}
        inner = r2.get("result") if isinstance(r2.get("result"), dict) else r2
        _rec(
            "r4_reset_approve",
            "sandbox.approve",
            "pass" if inner.get("success") else "review",
            {"sources_after_reset": len(inner.get("sources", [])) if isinstance(inner, dict) else None, "ack": r2},
        )
    else:
        _rec("r4_reset_approve", "info.sources.reset", "not_pending", {"resp": r, "elapsed": dt})


async def test_pgD_items_write_chain_no_rows(rpc):
    """info.items 写方法在空库下的诚实形态（invalid item_id 还是假成功）。"""
    for m, p in [
        ("info.items.toggle_read", {"item_id": "999999"}),
        ("info.items.toggle_saved", {"item_id": "999999"}),
        ("info.items.update", {"item_id": "999999", "notes": "x"}),
        ("info.items.save_to_note", {"item_id": "999999"}),
    ]:
        resp, dt = await _timed(rpc, m, p)
        r = _result(resp) or {}
        honest = (r.get("item") is None) or (r.get("success") is False)
        _rec(
            f"items::{m}",
            m,
            "honest_no_row" if honest else "phantom_success",
            {"resp_shape": {k: type(v).__name__ for k, v in r.items()}, "elapsed": dt},
        )


async def test_pgE_info_remaining(rpc):
    """info 剩余：auto_config / set_section_keywords / sections.create→update→delete / summarize。"""
    resp, dt = await _timed(rpc, "info.auto_config", {"enabled": False, "interval_minutes": 60})
    _rec("info_auto_config_off", "info.auto_config", "pass" if isinstance(_result(resp), dict) else "review", {"resp": _result(resp), "elapsed": dt})

    resp, dt = await _timed(rpc, "info.sections.create", {"name": "PG探针板块", "keywords": ["kubernetes"], "sources": ["hackernews"]})
    r = _result(resp) or {}
    sid = (r.get("section") or {}).get("id")
    _rec(
        "sections.create",
        "info.sections.create",
        "pass" if r.get("success") and sid else "fail",
        {"section_id": sid, "resp_error": r.get("error"), "elapsed": dt},
    )
    if sid:
        resp, _ = await _timed(rpc, "info.sections.update", {"section_id": sid, "enabled": False})
        r = _result(resp) or {}
        _rec("sections.update", "info.sections.update", "pass" if r.get("success") else "review", {"resp_error": r.get("error")})
        resp, _ = await _timed(rpc, "info.sections.list", {})
        eff = (_result(resp) or {}).get("effective") or []
        still = [s for s in eff if s.get("id") == sid]
        _rec(
            "sections.update_readback",
            "info.sections.list",
            "pass" if (not still or still[0].get("enabled") is False) else "write_lost",
            {"effective_has_section": bool(still), "enabled": (still[0].get("enabled") if still else None)},
        )
        resp, _ = await _timed(rpc, "info.sections.delete", {"section_id": sid})
        r = _result(resp) or {}
        if r.get("pending"):
            ack, _ = await _timed(rpc, "sandbox.approve", {"pending_id": r["pending_id"]})
            r = _result(ack) or {}
        _rec("sections.delete", "info.sections.delete", "pass" if r.get("success") else "review", {"resp_error": r.get("error")})

    resp, dt = await _timed(rpc, "info.summarize", {"title": "测试标题", "summary": "测试摘要 python"})
    r = _result(resp) or {}
    _rec(
        "info_summarize_no_ai",
        "info.summarize",
        "honest_ai_unavailable" if (r.get("error") or (not r.get("summary"))) else "review",
        {"resp": r, "elapsed": dt},
    )

    resp, dt = await _timed(rpc, "info.set_section_keywords", {"keywords": {"ai": ["大模型"]}})
    _rec(
        "info_set_section_keywords",
        "info.set_section_keywords",
        "pass" if isinstance(_result(resp), dict) else "review",
        {"resp": _result(resp), "elapsed": dt},
    )
    # 写后读
    resp, _ = await _timed(rpc, "info.get_section_keywords", {})
    kw = (_result(resp) or {}).get("keywords") or {}
    _rec(
        "info_get_section_keywords_readback",
        "info.get_section_keywords",
        "pass" if kw.get("ai") == ["大模型"] else "write_lost",
        {"ai": kw.get("ai")},
    )

    resp, dt = await _timed(rpc, "info.sections.ai_suggest_keywords", {"name": "量子计算"})
    r = _result(resp) or {}
    _rec(
        "sections.ai_suggest_keywords",
        "info.sections.ai_suggest_keywords",
        "review" if r else "fail",
        {"resp_shape": _result(resp), "error": _err(resp), "elapsed": dt},
    )


async def test_pgF_brief_offline_form(rpc):
    """info.brief 全量采集在无网隔离形态：errors 是否诚实呈现给用户。"""
    resp, dt = await _timed(rpc, "info.brief", {"no_ai": True})
    r = _result(resp) or {}
    errs = r.get("errors") or []
    _rec(
        "info_brief_all_offline",
        "info.brief",
        "honest_errors" if errs else "silent_empty",
        {"items_len": len(r.get("items") or []), "errors_count": len(errs), "error_sample": errs[:3], "elapsed": dt},
    )


def test_pgZ_dump():
    with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(_ledger, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[PG2-LEDGER] written {_LEDGER_PATH} ({len(_ledger)} entries)")
