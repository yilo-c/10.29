"""Monthly letter tests — 月度信生成器 + 调度管线（T7.1）。

覆盖：MonthlyLetterGenerator 生成 monthly 叙事并落 NarrativeStore；
narrative_scheduler 的月度管线（生成+投递收件箱）、跨月去重持久化、
手动触发 generate_monthly_now 不污染周度标记。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend import narrative_scheduler
from memory.narrative.monthly_letter import MonthlyLetterGenerator
from memory.narrative.narrative_store import NarrativeStore


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


def _make_context(data_dir: str, memories=None, notes=None) -> object:
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
    ctx._inbox = inbox
    return ctx


def _sample_memories() -> list[dict[str, Any]]:
    return [
        {"id": "m1", "content": "这月我决定开始系统复习英语，每天背 30 个单词。", "created_at": "2026-08-05T10:00:00+00:00"},
        {"id": "m2", "content": "完成了一个重要的里程碑：项目上线。", "created_at": "2026-08-12T10:00:00+00:00"},
        {"id": "m3", "content": "这个月有点忙，但进展很大。", "created_at": "2026-08-20T10:00:00+00:00"},
    ]


# ── MonthlyLetterGenerator 单元 ──


@pytest.mark.asyncio
async def test_generate_monthly_letter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 时间炸弹修复：month_label 来自真实时钟（monthly_letter.generate 内
    # datetime.now(timezone.utc)），样例记忆固定在 2026-08——不钉住时钟的话
    # 该断言每月只在一两个月内碰巧成立。替换模块内 datetime 引用，
    # 把"当前时间"钉在与样例记忆同月，断言与真实日期彻底解耦。
    from memory.narrative import monthly_letter as monthly_letter_mod

    class _FixedDatetime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 8, 15, 12, 0, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(monthly_letter_mod, "datetime", _FixedDatetime)

    store = NarrativeStore(str(tmp_path))
    gen = MonthlyLetterGenerator(narrative_store=store)

    narrative = await gen.generate(_sample_memories())

    assert narrative is not None
    assert narrative["narrative_type"] == "monthly"
    assert narrative["month_label"]
    assert narrative["month_label"] == "2026-08"
    assert narrative["item_count"] == 3
    # 已持久化到 NarrativeStore
    assert store.count() == 1
    latest = store.get_latest_of_type("monthly")
    assert latest["month_label"] == narrative["month_label"]


@pytest.mark.asyncio
async def test_generate_monthly_no_data_returns_none(tmp_path: Path) -> None:
    store = NarrativeStore(str(tmp_path))
    gen = MonthlyLetterGenerator(narrative_store=store)

    narrative = await gen.generate([], notes=[])
    assert narrative is None
    assert store.count() == 0


@pytest.mark.asyncio
async def test_generate_monthly_milestones_title(tmp_path: Path) -> None:
    store = NarrativeStore(str(tmp_path))
    gen = MonthlyLetterGenerator(narrative_store=store)
    narrative = await gen.generate(_sample_memories())
    assert "值得记住" in narrative["title"]
    assert narrative["milestones"]


# ── narrative_scheduler 月度管线 ──


@pytest.mark.asyncio
async def test_scheduler_run_monthly_generates_and_posts(tmp_path: Path) -> None:
    ctx = _make_context(str(tmp_path), memories=_sample_memories())
    narrative_scheduler.set_context(ctx)

    result = await narrative_scheduler._run_monthly()

    assert result["success"] is True
    assert result["month_label"]
    # 投递到收件箱
    assert len(ctx._inbox.entries) == 1
    entry = ctx._inbox.entries[0]
    assert entry["kind"] == "narrative"
    assert entry["title"].startswith("📮")
    # 去重标记已持久化
    assert ctx.local_data_store.load_json(narrative_scheduler._PERSIST_KEY_MONTHLY) == {
        "month": result["month_label"]
    }


@pytest.mark.asyncio
async def test_scheduler_generate_monthly_now_no_pollution(tmp_path: Path) -> None:
    """generate_monthly_now 不改周度去重标记。"""
    narrative_scheduler._last_run_week = "2026-W33"
    ctx = _make_context(str(tmp_path), memories=_sample_memories())

    narrative = await narrative_scheduler.generate_monthly_now(ctx)

    assert narrative is not None
    assert narrative["narrative_type"] == "monthly"
    assert narrative_scheduler._last_run_week == "2026-W33"  # 周度标记未被污染
    assert len(ctx._inbox.entries) == 1
