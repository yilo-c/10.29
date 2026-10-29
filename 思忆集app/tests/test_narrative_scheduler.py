"""Narrative scheduler unit tests — 周度叙事生成调度器。

覆盖：有数据时生成并投递收件箱、无数据时跳过、_run_weekly 持久化 week_label、
启动关闭生命周期（configure/restart/shutdown）。
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import pytest

from backend import narrative_scheduler


# ── Fake context ────────────────────────────────────────────────


class _FakeDataStore:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._store: dict[str, Any] = {}

    def save_json(self, key: str, data: Any) -> None:
        self._store[key] = data

    def load_json(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)


class _FakeMemoryIndex:
    def __init__(self, items: list[dict[str, Any]]):
        self._items = items

    def recall(self, query: str, top_k: int = 8, **kwargs: Any) -> dict[str, Any]:
        return {"items": self._items, "count": len(self._items)}


class _FakeNoteRepo:
    def __init__(self, notes: list[dict[str, Any]]):
        self._notes = notes

    def list_notes(self) -> list[dict[str, Any]]:
        return self._notes


class _FakeInboxRepo:
    def __init__(self):
        self.entries: list[dict[str, Any]] = []

    def add_entry(self, entry: dict[str, Any]) -> int:
        self.entries.append(entry)
        return len(self.entries)


def _make_context(
    data_dir: str,
    memories: list[dict[str, Any]] | None = None,
    notes: list[dict[str, Any]] | None = None,
) -> object:
    inbox = _FakeInboxRepo()
    ctx = type(
        "FakeContext",
        (),
        {
            "local_data_store": _FakeDataStore(data_dir),
            "memory_index_service": _FakeMemoryIndex(memories or []),
            "personal_note_repository": _FakeNoteRepo(notes or []),
            "personal_inbox_repository": inbox,
        },
    )()
    ctx._inbox = inbox  # 便于断言
    return ctx


def _sample_memories() -> list[dict[str, Any]]:
    return [
        {"id": "m1", "content": "这周我决定开始系统复习英语，制定了每天背 30 个单词的计划。", "created_at": "2026-08-21T10:00:00+00:00"},
        {"id": "m2", "content": "今天有点焦虑，担心项目进度，但后来放松了一些。", "created_at": "2026-08-22T10:00:00+00:00"},
    ]


# ── Tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_and_post_with_data(tmp_path: Path) -> None:
    ctx = _make_context(str(tmp_path), memories=_sample_memories())
    narrative_scheduler.set_context(ctx)

    narrative = await narrative_scheduler._generate_and_post(ctx)

    assert narrative is not None
    assert narrative["narrative_type"] == "weekly"
    assert narrative["week_label"]
    assert narrative["item_count"] == 2
    # 投递到收件箱
    assert len(ctx._inbox.entries) == 1
    entry = ctx._inbox.entries[0]
    assert entry["kind"] == "narrative"
    assert entry["title"].startswith("📖")
    assert entry["content"]
    # 叙事已持久化到 NarrativeStore
    from memory.narrative.narrative_store import NarrativeStore
    store = NarrativeStore(tmp_path)
    assert store.count() == 1


@pytest.mark.asyncio
async def test_skip_when_no_data(tmp_path: Path) -> None:
    ctx = _make_context(str(tmp_path), memories=[], notes=[])
    narrative_scheduler.set_context(ctx)

    narrative = await narrative_scheduler._generate_and_post(ctx)

    assert narrative is None
    assert len(ctx._inbox.entries) == 0


@pytest.mark.asyncio
async def test_run_weekly_persists_week_label(tmp_path: Path) -> None:
    ctx = _make_context(str(tmp_path), memories=_sample_memories())
    narrative_scheduler.set_context(ctx)

    await narrative_scheduler._run_weekly()

    assert narrative_scheduler._last_run_week
    # 持久化到 data store
    assert ctx.local_data_store.load_json(narrative_scheduler._PERSIST_KEY) == {
        "week": narrative_scheduler._last_run_week
    }


def test_configure_and_restart(tmp_path: Path) -> None:
    ctx = _make_context(str(tmp_path), memories=[])
    narrative_scheduler.set_context(ctx)
    narrative_scheduler.configure(enabled=True)
    assert narrative_scheduler.is_enabled()

    # 使用独立持久事件循环，避免受 pytest-asyncio AUTO 模式对其他用例的
    # 默认循环清理影响；保持循环存活以便断言后台任务处于 pending 状态。
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(narrative_scheduler.restart())
        assert narrative_scheduler._scheduler_task is not None
        assert not narrative_scheduler._scheduler_task.done()

        loop.run_until_complete(narrative_scheduler.shutdown())
        assert narrative_scheduler._scheduler_task is None
    finally:
        loop.close()
        asyncio.set_event_loop(None)


# ── M1 补跑回归（2026-08-29）──


@pytest.mark.asyncio
async def test_loop_runs_weekly_catchup_regardless_of_weekday(monkeypatch) -> None:
    """周报补跑不得要求"必须在周一在线"（M1）。

    旧实现只在 now.weekday() == 0 时触发，而实测进程整周启动都落在周二/三/五，
    导致 data/data/narratives/ 始终为空、周报从未产出。现版只要
    「当前 ISO 周 ≠ 上次成功生成的周」就补跑，且启动时立即检查不等满一小时。
    """
    calls: list[str] = []

    async def _fake_run_weekly() -> dict:
        calls.append("weekly")
        narrative_scheduler._last_run_week = "2026-W99"
        return {"success": True}

    async def _fake_run_monthly() -> dict:
        calls.append("monthly")
        return {"success": True}

    async def _stop_after_first_pass(_seconds: float) -> None:
        narrative_scheduler._enabled = False  # 让循环跑完一轮即退出

    monkeypatch.setattr(narrative_scheduler, "_run_weekly", _fake_run_weekly)
    monkeypatch.setattr(narrative_scheduler, "_run_monthly", _fake_run_monthly)
    monkeypatch.setattr(narrative_scheduler.asyncio, "sleep", _stop_after_first_pass)
    monkeypatch.setattr(narrative_scheduler, "_current_week_label", lambda: "2026-W99")

    narrative_scheduler._last_run_week = None  # 从未生成过
    narrative_scheduler._last_run_month = narrative_scheduler._current_month_label()
    narrative_scheduler._enabled = True

    await narrative_scheduler._loop()

    assert calls == ["weekly"], f"非周一也应补跑周报，实际调用：{calls}"


@pytest.mark.asyncio
async def test_loop_skips_when_week_already_generated(monkeypatch) -> None:
    """同一 ISO 周内已成功生成过则不得重复生成。"""
    calls: list[str] = []

    async def _fake_run_weekly() -> dict:
        calls.append("weekly")
        return {"success": True}

    async def _stop_after_first_pass(_seconds: float) -> None:
        narrative_scheduler._enabled = False

    monkeypatch.setattr(narrative_scheduler, "_run_weekly", _fake_run_weekly)
    monkeypatch.setattr(narrative_scheduler.asyncio, "sleep", _stop_after_first_pass)

    narrative_scheduler._last_run_week = narrative_scheduler._current_week_label()
    narrative_scheduler._last_run_month = narrative_scheduler._current_month_label()
    narrative_scheduler._enabled = True

    await narrative_scheduler._loop()

    assert calls == [], f"同周重复生成：{calls}"


@pytest.mark.asyncio
async def test_generate_now_on_demand(tmp_path: Path) -> None:
    """generate_now 提供按需触发，复用同一生成管线但不改周度去重标记。"""
    ctx = cancel_last = None
    # 模拟已去重的周标记，确认按需生成不会污染去重状态
    narrative_scheduler._last_run_week = "2026-W33"
    ctx = _make_context(str(tmp_path), memories=_sample_memories())

    narrative = await narrative_scheduler.generate_now(ctx)

    assert narrative is not None
    assert narrative["narrative_type"] == "weekly"
    assert narrative["week_label"]
    # 去重标记未被改动
    assert narrative_scheduler._last_run_week == "2026-W33"
    # 仍会投递收件箱并持久化
    assert len(ctx._inbox.entries) == 1
    from memory.narrative.narrative_store import NarrativeStore
    assert NarrativeStore(tmp_path).count() == 1
