"""Sprint 1B — DueReminderScheduler unit tests."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from core.events import AppEvent, event_bus
from backend.due_reminder import DueReminderScheduler, REMINDER_KEY


class _FakeDataStore:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def load_json(self, key: str, default):
        return self._data.get(key, default)

    def save_json(self, key: str, data) -> None:
        self._data[key] = data


class _FakeRepo:
    def __init__(self, words: list[dict]) -> None:
        self._words = words

    def list_words(self):
        return list(self._words)


class _FakeContext:
    def __init__(self, words: list[dict]) -> None:
        self.local_data_store = _FakeDataStore()
        self.vocabulary_repository = _FakeRepo(words)


def _capture(event_type: str, captured: list[AppEvent]):
    def _cb(e: AppEvent) -> None:
        if e.event_type == event_type:
            captured.append(e)
    return _cb


@pytest.mark.asyncio
async def test_emits_when_due_words_exist():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    ctx = _FakeContext([
        {"id": 1, "word": "abandon", "next_review": past},
        {"id": 2, "word": "aboard", "next_review": past},
    ])
    captured: list[AppEvent] = []
    cb = _capture("vocabulary.due_reminder", captured)
    event_bus.on("vocabulary.due_reminder", cb)
    try:
        sched = DueReminderScheduler()
        sched._context = ctx
        await sched._tick()
    finally:
        event_bus.off("vocabulary.due_reminder", cb)

    assert len(captured) == 1
    assert captured[0].payload["due_count"] == 2
    state = ctx.local_data_store.load_json(REMINDER_KEY, {})
    assert state.get("last_reminded_date")


@pytest.mark.asyncio
async def test_no_emit_when_no_due_words():
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    ctx = _FakeContext([{"id": 1, "word": "future_word", "next_review": future}])
    captured: list[AppEvent] = []
    cb = _capture("vocabulary.due_reminder", captured)
    event_bus.on("vocabulary.due_reminder", cb)
    try:
        sched = DueReminderScheduler()
        sched._context = ctx
        await sched._tick()
    finally:
        event_bus.off("vocabulary.due_reminder", cb)

    assert not captured


@pytest.mark.asyncio
async def test_no_emit_twice_same_day():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    ctx = _FakeContext([{"id": 1, "word": "abandon", "next_review": past}])
    captured: list[AppEvent] = []
    cb = _capture("vocabulary.due_reminder", captured)
    event_bus.on("vocabulary.due_reminder", cb)
    try:
        sched = DueReminderScheduler()
        sched._context = ctx
        await sched._tick()
        await sched._tick()  # 同一天，第二次应被去重
    finally:
        event_bus.off("vocabulary.due_reminder", cb)

    assert len(captured) == 1


@pytest.mark.asyncio
async def test_message_scales_with_count():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    ctx_small = _FakeContext([{"id": i, "word": f"w{i}", "next_review": past} for i in range(3)])
    ctx_large = _FakeContext([{"id": i, "word": f"w{i}", "next_review": past} for i in range(35)])
    # 模拟非首次：让 _compose_message 走 >=30 分支
    ctx_large.local_data_store.save_json(REMINDER_KEY, {"last_reminded_date": "2026-01-01"})

    out: list[AppEvent] = []
    cb = _capture("vocabulary.due_reminder", out)
    event_bus.on("vocabulary.due_reminder", cb)
    try:
        s1 = DueReminderScheduler(); s1._context = ctx_small
        await s1._tick()
        s2 = DueReminderScheduler(); s2._context = ctx_large
        await s2._tick()
    finally:
        event_bus.off("vocabulary.due_reminder", cb)

    assert len(out) == 2
    assert "3" in out[0].payload["message"]
    assert "35" in out[1].payload["message"]
    assert "堆成山" in out[1].payload["message"]
