"""Sprint 1A — vocab_mood coordinator unit tests.

验证：
  1. 连续 ≥3 forget → 派发 mood.changed=alert
  2. 5 词全对 → 派发 mood.changed=happy（且总数刚好越过阈值时只触发一次）
  3. session 闲置 >90s → 派发 vocabulary.session.summary
  4. session reset 后 happy 阈值可重新触发
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from core.events import AppEvent, event_bus
from backend.vocab_mood import VocabMoodCoordinator


def _record(coord, *, quality, word="abandon", at: datetime | None = None) -> None:
    """直接调内部回调，绕开 EventBus 全局，便于断言。"""
    when = (at or datetime.now(timezone.utc)).isoformat()
    coord._on_review(AppEvent(
        event_type="vocabulary.review.recorded",
        payload={"word_id": 1, "word": word, "quality": quality},
        source="test",
        created_at=when,
    ))


@pytest.fixture
def coord_with_capture():
    """构造一个独立 coordinator + 捕获其派生事件。"""
    captured: list[AppEvent] = []

    def _capture(event: AppEvent) -> None:
        captured.append(event)

    event_bus.on("mood.changed", _capture)
    event_bus.on("vocabulary.session.summary", _capture)

    coord = VocabMoodCoordinator()

    yield coord, captured

    event_bus.off("mood.changed", _capture)
    event_bus.off("vocabulary.session.summary", _capture)


def test_consecutive_forget_emits_alert(coord_with_capture):
    coord, captured = coord_with_capture
    base = datetime.now(timezone.utc)
    for i in range(3):
        _record(coord, quality=1, word=f"w{i}", at=base + timedelta(seconds=i * 5))

    moods = [e for e in captured if e.event_type == "mood.changed"]
    assert any(
        e.payload.get("mood") == "alert" and e.payload.get("reason") == "vocab_consecutive_forget"
        for e in moods
    ), f"expected concerned mood after 3 forgets; got {[e.payload for e in moods]}"


def test_two_forgets_no_alert(coord_with_capture):
    coord, captured = coord_with_capture
    base = datetime.now(timezone.utc)
    for i in range(2):
        _record(coord, quality=1, word=f"w{i}", at=base + timedelta(seconds=i * 5))
    assert not [e for e in captured if e.event_type == "mood.changed"]


def test_perfect_session_emits_happy(coord_with_capture):
    coord, captured = coord_with_capture
    base = datetime.now(timezone.utc)
    for i in range(5):
        _record(coord, quality=5, word=f"w{i}", at=base + timedelta(seconds=i * 5))

    moods = [e for e in captured if e.event_type == "mood.changed"]
    happy = [e for e in moods if e.payload.get("reason") == "vocab_session_perfect"]
    assert len(happy) == 1, f"expected exactly one happy event, got {happy}"
    assert happy[0].payload.get("mood") == "happy"
    assert happy[0].payload.get("count") == 5


def test_perfect_session_only_fires_once(coord_with_capture):
    coord, captured = coord_with_capture
    base = datetime.now(timezone.utc)
    # 7 词全对，仅在第 5 词时发一次
    for i in range(7):
        _record(coord, quality=5, word=f"w{i}", at=base + timedelta(seconds=i * 5))
    happy = [e for e in captured if e.payload.get("reason") == "vocab_session_perfect"]
    assert len(happy) == 1


def test_idle_timeout_emits_session_summary(coord_with_capture):
    coord, captured = coord_with_capture
    base = datetime.now(timezone.utc)
    _record(coord, quality=4, word="aaa", at=base)
    _record(coord, quality=4, word="bbb", at=base + timedelta(seconds=10))
    # 距上次 100s（>90），新评分会触发上一段 summary
    _record(coord, quality=5, word="ccc", at=base + timedelta(seconds=110))

    summaries = [e for e in captured if e.event_type == "vocabulary.session.summary"]
    assert summaries, "expected a session summary on idle timeout"
    assert summaries[0].payload.get("total") == 2
    assert summaries[0].payload.get("ended_reason") == "idle_timeout"


def test_forget_then_correct_resets_streak(coord_with_capture):
    coord, captured = coord_with_capture
    base = datetime.now(timezone.utc)
    # 2 forget + 1 correct + 2 forget → 不应触发 alert
    for q in [1, 1, 5, 1, 1]:
        _record(coord, quality=q, word="x", at=base + timedelta(seconds=1))
        base += timedelta(seconds=5)
    moods = [e for e in captured if e.event_type == "mood.changed"]
    assert not [e for e in moods if e.payload.get("reason") == "vocab_consecutive_forget"]
