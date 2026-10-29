# -*- coding: utf-8 -*-
"""U4（2026-09-16）：modules.status RPC —— 前端隐藏机制的数据源。

契约：``modules.status {}`` → ``{"success": true, "modules": [
{"module": str, "configured": bool, "hasData": bool}, ...]}``，
覆盖 reminder / reading / external_content。

前端判定式 configured=false 才隐藏 ⇒ 后端保证：
- hasData=true 时 configured 宽松为 true；
- 任何子探测异常 → 该模块 configured=true 保底（绝不因探测失败误隐藏）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._modules_status import register_modules_status
from backend.method_registry import MethodRegistry


# ── fakes ──


class _FakeSettingsRepo:
    def __init__(self, values: dict | None = None, boom: bool = False) -> None:
        self._values = values or {}
        self._boom = boom

    def get_setting(self, key, default=None):
        if self._boom:
            raise RuntimeError("settings boom")
        return self._values.get(key, default)


class _FakeTaskRepo:
    def __init__(self, tasks: list | None = None, boom: bool = False) -> None:
        self._tasks = tasks if tasks is not None else []
        self._boom = boom

    def list_tasks(self, include_deleted: bool = False):
        if self._boom:
            raise RuntimeError("task repo boom")
        return list(self._tasks)


def _ctx(tmp_path, settings_values=None, tasks=None, boom_settings=False, boom_tasks=False):
    return SimpleNamespace(
        local_data_store=SimpleNamespace(base_dir=str(tmp_path)),
        settings_repository=_FakeSettingsRepo(settings_values, boom=boom_settings),
        personal_task_repository=_FakeTaskRepo(tasks, boom=boom_tasks),
    )


def _reg(ctx):
    reg = MethodRegistry()
    register_modules_status(reg, ctx)
    return reg


# ══════════════════════════════════════
# 1. 信封形状
# ══════════════════════════════════════


@pytest.mark.asyncio
async def test_envelope_shape(tmp_path):
    reg = _reg(_ctx(tmp_path))
    out = await reg.dispatch("modules.status", {})
    assert out["success"] is True
    assert isinstance(out["modules"], list)
    names = [m["module"] for m in out["modules"]]
    assert set(names) >= {"reminder", "reading", "external_content"}
    for m in out["modules"]:
        assert set(m.keys()) == {"module", "configured", "hasData"}
        assert isinstance(m["module"], str)
        assert isinstance(m["configured"], bool)
        assert isinstance(m["hasData"], bool)


# ══════════════════════════════════════
# 2. external_content configured 判定
# ══════════════════════════════════════


@pytest.mark.asyncio
async def test_external_content_unconfigured_when_no_keys(tmp_path):
    """无任何外部凭据/导入目录、无沉淀文章 → configured=False（前端隐藏该入口）。"""
    reg = _reg(_ctx(tmp_path))
    out = await reg.dispatch("modules.status", {})
    ec = next(m for m in out["modules"] if m["module"] == "external_content")
    assert ec["configured"] is False and ec["hasData"] is False


@pytest.mark.asyncio
async def test_external_content_configured_with_readwise_token(tmp_path):
    reg = _reg(_ctx(tmp_path, settings_values={"readwise_token": "some-encrypted-or-plain-token"}))
    out = await reg.dispatch("modules.status", {})
    ec = next(m for m in out["modules"] if m["module"] == "external_content")
    assert ec["configured"] is True


@pytest.mark.asyncio
async def test_external_content_configured_true_when_has_data(tmp_path):
    """无 token 但已沉淀外部文章 → hasData=True ⇒ configured 宽松为 True（契约）。"""
    (tmp_path / "saved_articles.json").write_text(
        json.dumps({"articles": {"readwise_1": {"title": "x"}}, "order": ["readwise_1"]}),
        encoding="utf-8",
    )
    reg = _reg(_ctx(tmp_path))
    out = await reg.dispatch("modules.status", {})
    ec = next(m for m in out["modules"] if m["module"] == "external_content")
    assert ec["hasData"] is True
    assert ec["configured"] is True, "hasData=true 时 configured 必须宽松为 true"


# ══════════════════════════════════════
# 3. reminder / reading 判定
# ══════════════════════════════════════


@pytest.mark.asyncio
async def test_reminder_has_data_detects_reminder_time_tasks(tmp_path):
    tasks = [
        {"id": 1, "title": "普通任务", "reminder_time": None},
        {"id": 2, "title": "提醒", "reminder_time": 1_700_000_000_000},
    ]
    reg = _reg(_ctx(tmp_path, tasks=tasks))
    out = await reg.dispatch("modules.status", {})
    rm = next(m for m in out["modules"] if m["module"] == "reminder")
    assert rm["configured"] is True and rm["hasData"] is True


@pytest.mark.asyncio
async def test_reminder_empty_tasks_has_data_false(tmp_path):
    reg = _reg(_ctx(tmp_path, tasks=[{"id": 1, "title": "无提醒"}]))
    out = await reg.dispatch("modules.status", {})
    rm = next(m for m in out["modules"] if m["module"] == "reminder")
    assert rm["configured"] is True and rm["hasData"] is False


@pytest.mark.asyncio
async def test_reading_has_data_with_positions_file(tmp_path):
    (tmp_path / "reading_positions.json").write_text(json.dumps({"note-1": {"scroll": 123, "updated_at": 1}}), encoding="utf-8")
    reg = _reg(_ctx(tmp_path))
    out = await reg.dispatch("modules.status", {})
    rd = next(m for m in out["modules"] if m["module"] == "reading")
    assert rd["configured"] is True and rd["hasData"] is True


# ══════════════════════════════════════
# 4. 注册形状（schema 棘轮口径）
# ══════════════════════════════════════


def test_registered_with_schema():
    """新注册必须走 register_with_schema —— schema_coverage_ratchet 的硬要求。"""

    class _Recording:
        def __init__(self):
            self.plain: dict = {}
            self.with_schema: dict = {}

        def register(self, name, fn):
            self.plain[name] = fn

        def register_with_schema(self, name, fn, schema=None, **kw):
            self.with_schema[name] = schema

    rec = _Recording()
    register_modules_status(rec, SimpleNamespace())
    assert "modules.status" in rec.with_schema, "modules.status 未以 register_with_schema 注册"
    schema = rec.with_schema["modules.status"]
    assert isinstance(schema, dict) and schema.get("type") == "object"
    assert "modules.status" not in rec.plain
    # MethodRegistry 真实注册面也可见
    reg = _reg(SimpleNamespace())
    assert reg.has_method("modules.status")
    assert reg.get_schema("modules.status") is not None


# ══════════════════════════════════════
# 5. 降级不崩溃：探测异常 → configured 保底 True
# ══════════════════════════════════════


@pytest.mark.asyncio
async def test_probe_exception_degrades_to_configured_true(tmp_path):
    """任务仓储抛异常：reminder 整体不崩、configured 保底 True（不误隐藏）。"""
    reg = _reg(_ctx(tmp_path, boom_tasks=True))
    out = await reg.dispatch("modules.status", {})
    assert out["success"] is True
    rm = next(m for m in out["modules"] if m["module"] == "reminder")
    assert rm["configured"] is True, "探测失败必须 configured=True 保底"


@pytest.mark.asyncio
async def test_settings_exception_does_not_hide_external_content(tmp_path):
    """settings_repository 抛异常：external_content 探测失败也保底 configured=True。"""
    reg = _reg(_ctx(tmp_path, boom_settings=True))
    out = await reg.dispatch("modules.status", {})
    assert out["success"] is True
    ec = next(m for m in out["modules"] if m["module"] == "external_content")
    assert ec["configured"] is True


@pytest.mark.asyncio
async def test_missing_repos_never_crash(tmp_path):
    """context 完全裸（无任何仓储属性）→ 三模块仍返回、不抛。"""
    reg = _reg(SimpleNamespace(local_data_store=SimpleNamespace(base_dir=str(tmp_path))))
    out = await reg.dispatch("modules.status", {})
    assert out["success"] is True and len(out["modules"]) >= 3
