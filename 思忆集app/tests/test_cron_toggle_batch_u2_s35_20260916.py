# -*- coding: utf-8 -*-
"""U2（S3-5，2026-09-16）：一键静音自主任务的后端批量原语。

方案 §6 声称"cron 批量 disable 后端方法已在"不实——实测只有单条
toggle_job(cron_service.py:479)/cron.toggle。本批补通用批量原语
toggle_jobs（一次锁、一次落盘）+ RPC cron.toggle_batch，供前端按分类
挑选 job_ids 后一键静音/恢复。后端只给通用批量能力，"自主任务"分组
是前端语义，不在后端硬编码。
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from data.local_storage import LocalDataStore
from services.cron_service import CronService


class _SettingsRepo:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


def _make_service(tmp_path):
    return CronService(
        data_store=LocalDataStore(base_dir=str(tmp_path)),
        executor=lambda action_id, payload: {"ok": True},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )


# ── service 层 toggle_jobs 批量原语 ──


def test_toggle_jobs_disables_multiple(tmp_path):
    service = _make_service(tmp_path)
    a = service.add_job("早报", "subagent.spawn", {}, schedule="0 8 * * *", repeat="cron")
    b = service.add_job("复盘", "journal.daily_draft", {}, schedule="0 21 * * *", repeat="cron")
    c = service.add_job("备份", "system.auto_backup", {}, schedule="0 3 * * 0", repeat="cron")

    result = service.toggle_jobs([a["job_id"], b["job_id"]], enabled=False)

    assert len(result["changed"]) == 2
    assert result["not_found"] == []
    jobs = {j["job_id"]: j for j in service.list_jobs(include_disabled=True)}
    assert jobs[a["job_id"]]["enabled"] is False
    assert jobs[b["job_id"]]["enabled"] is False
    assert jobs[c["job_id"]]["enabled"] is True  # 未选中的不受影响


def test_toggle_jobs_persists(tmp_path):
    """批量禁用必须落盘，重载后仍为禁用（一键静音跨重启有效）。"""
    service = _make_service(tmp_path)
    a = service.add_job("早报", "subagent.spawn", {}, schedule="0 8 * * *", repeat="cron")
    service.toggle_jobs([a["job_id"]], enabled=False)

    reloaded = CronService(
        data_store=LocalDataStore(base_dir=str(tmp_path)),
        executor=lambda action_id, payload: {"ok": True},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )
    jobs = {j["job_id"]: j for j in reloaded.list_jobs(include_disabled=True)}
    assert jobs[a["job_id"]]["enabled"] is False


def test_toggle_jobs_idempotent_state_skip(tmp_path):
    """已处于目标态的 job 不计入 changed（避免无谓落盘/调度刷新）。"""
    service = _make_service(tmp_path)
    a = service.add_job("早报", "subagent.spawn", {}, schedule="0 8 * * *", repeat="cron")
    # 新建即 enabled=True，再要求 disable=True（目标态与现状一致）
    result = service.toggle_jobs([a["job_id"]], enabled=True)
    assert result["changed"] == []


def test_toggle_jobs_reports_not_found(tmp_path):
    service = _make_service(tmp_path)
    a = service.add_job("早报", "subagent.spawn", {}, schedule="0 8 * * *", repeat="cron")
    result = service.toggle_jobs([a["job_id"], "ghost-id"], enabled=False)
    assert a["job_id"] in [c["job_id"] for c in result["changed"]]
    assert result["not_found"] == ["ghost-id"]


def test_toggle_jobs_empty_input(tmp_path):
    service = _make_service(tmp_path)
    result = service.toggle_jobs([], enabled=False)
    assert result["changed"] == []
    assert result["not_found"] == []


# ── RPC cron.toggle_batch 端到端 ──


@pytest.mark.asyncio
async def test_rpc_toggle_batch(tmp_path):
    from types import SimpleNamespace

    from backend.method_registry import MethodRegistry
    from backend.handlers._cron import register_cron

    service = _make_service(tmp_path)
    a = service.add_job("早报", "subagent.spawn", {}, schedule="0 8 * * *", repeat="cron")
    b = service.add_job("复盘", "journal.daily_draft", {}, schedule="0 21 * * *", repeat="cron")

    reg = MethodRegistry()
    ctx = SimpleNamespace(cron_service=service)
    register_cron(reg, ctx)
    handler = reg._handlers["cron.toggle_batch"]

    r = await handler(job_ids=[a["job_id"], b["job_id"]], enabled=False)
    assert r["success"] is True
    assert r["changed_count"] == 2
    assert r["enabled"] is False


@pytest.mark.asyncio
async def test_rpc_toggle_batch_requires_ids(tmp_path):
    from types import SimpleNamespace

    from backend.method_registry import MethodRegistry
    from backend.handlers._cron import register_cron

    service = _make_service(tmp_path)
    reg = MethodRegistry()
    ctx = SimpleNamespace(cron_service=service)
    register_cron(reg, ctx)
    handler = reg._handlers["cron.toggle_batch"]

    r = await handler(job_ids=[], enabled=False)
    assert r["success"] is False
    assert "error" in r
