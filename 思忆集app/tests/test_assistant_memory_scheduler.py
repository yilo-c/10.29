from __future__ import annotations

import sys
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from memory.scheduler import (
    INITIAL_INTERVAL_DAYS,
    SECOND_INTERVAL_DAYS,
    THIRD_INTERVAL_DAYS,
    DEFAULT_EASINESS,
    MIN_EASINESS,
    DEFAULT_MAX_INTERVAL,
    ScheduledMemory,
    ReviewSettings,
    MemoryScheduler,
)


class _FakeDataStore:
    """In-memory data store for testing."""
    def __init__(self):
        self._data = {}

    def load_json(self, key, default=None):
        return self._data.get(key, default) if default is not None else self._data.get(key, {})

    def save_json(self, key, value):
        self._data[key] = value


def _make_scheduler():
    return MemoryScheduler(data_store=_FakeDataStore())


# ── ScheduledMemory data class ──

def test_scheduled_memory_defaults():
    m = ScheduledMemory(memory_id="test1", content="hello")
    assert m.memory_type == "factual"
    assert m.tags == []
    assert m.interval_days == INITIAL_INTERVAL_DAYS
    assert m.easiness == DEFAULT_EASINESS
    assert m.repetitions == 0
    assert m.review_count == 0
    assert m.last_quality == 0


def test_scheduled_memory_roundtrip():
    m = ScheduledMemory(
        memory_id="m1",
        content="test memory",
        memory_type="preference",
        tags=["tag1", "tag2"],
        interval_days=3,
        easiness=2.3,
        repetitions=2,
        review_count=5,
        last_quality=4,
    )
    d = m.to_dict()
    restored = ScheduledMemory.from_dict(d)
    assert restored.memory_id == "m1"
    assert restored.content == "test memory"
    assert restored.memory_type == "preference"
    assert restored.tags == ["tag1", "tag2"]
    assert restored.interval_days == 3
    assert restored.easiness == 2.3
    assert restored.repetitions == 2
    assert restored.review_count == 5
    assert restored.last_quality == 4


# ── ReviewSettings data class ──

def test_review_settings_defaults():
    s = ReviewSettings()
    assert s.review_hour == 9
    assert s.review_minute == 0
    assert s.daily_max_items == 15
    assert s.quiet_days == []
    assert s.enabled is True
    assert s.is_quiet_today() is False


def test_review_settings_roundtrip():
    s = ReviewSettings(
        review_hour=8, review_minute=30, daily_max_items=10,
        quiet_days=[5, 6], enabled=False,
    )
    d = s.to_dict()
    restored = ReviewSettings.from_dict(d)
    assert restored.review_hour == 8
    assert restored.review_minute == 30
    assert restored.daily_max_items == 10
    assert restored.quiet_days == [5, 6]
    assert restored.enabled is False


# ── Scheduler add / remove ──

def test_scheduler_add_memory():
    scheduler = _make_scheduler()
    m = scheduler.add_memory("mem1", "remember this", memory_type="factual", tags=["test"])
    assert m.memory_id == "mem1"
    assert m.content == "remember this"
    assert m.memory_type == "factual"
    assert m.tags == ["test"]
    assert m.interval_days == INITIAL_INTERVAL_DAYS


def test_scheduler_get_memory():
    scheduler = _make_scheduler()
    scheduler.add_memory("mem1", "test content")
    m = scheduler.get_memory("mem1")
    assert m is not None
    assert m.content == "test content"
    assert scheduler.get_memory("nonexistent") is None


def test_scheduler_remove_memory():
    scheduler = _make_scheduler()
    scheduler.add_memory("mem1", "test")
    assert scheduler.remove_memory("mem1") is True
    assert scheduler.remove_memory("mem1") is False


# ── SM-2: quality < 4 (not passed) should reset ──

def test_scheduler_review_quality_less_than_4_resets():
    scheduler = _make_scheduler()
    scheduler.add_memory("mem1", "test memory")
    # First pass to get repetitions>0
    scheduler.review("mem1", quality=4)
    # Then fail — should reset
    updated = scheduler.review("mem1", quality=3)
    assert updated is not None
    assert updated.repetitions == 0
    assert updated.interval_days == INITIAL_INTERVAL_DAYS
    assert updated.last_quality == 3


def test_scheduler_review_quality_4_plus():
    scheduler = _make_scheduler()
    scheduler.add_memory("mem1", "test memory")
    updated = scheduler.review("mem1", quality=4)
    assert updated is not None
    assert updated.repetitions == 1
    assert updated.review_count == 1
    assert updated.last_quality == 4
    assert updated.interval_days == INITIAL_INTERVAL_DAYS  # 1st pass: 1 day


def test_scheduler_review_spaced_repetition():
    scheduler = _make_scheduler()
    scheduler.add_memory("mem1", "test memory")

    # First review: quality 4 -> interval = 1 day, repetitions=1
    r1 = scheduler.review("mem1", quality=4)
    assert r1.interval_days == INITIAL_INTERVAL_DAYS  # 1
    assert r1.repetitions == 1

    # Second review: quality 5 -> interval = 3 days, repetitions=2
    r2 = scheduler.review("mem1", quality=5)
    assert r2.interval_days == SECOND_INTERVAL_DAYS  # 3
    assert r2.repetitions == 2

    # Third review: quality 4 -> interval = 7 days, repetitions=3
    r3 = scheduler.review("mem1", quality=4)
    assert r3.interval_days == THIRD_INTERVAL_DAYS  # 7
    assert r3.repetitions == 3

    # Fourth review: interval = round(7 * easiness)
    r4 = scheduler.review("mem1", quality=4)
    expected = round(THIRD_INTERVAL_DAYS * r4.easiness)
    assert r4.interval_days == expected
    assert r4.repetitions == 4


def test_scheduler_review_nonexistent():
    scheduler = _make_scheduler()
    result = scheduler.review("nonexistent", quality=4)
    assert result is None


# ── Quality clamping ──

def test_quality_clamped():
    scheduler = _make_scheduler()
    scheduler.add_memory("mem1", "test")
    r = scheduler.review("mem1", quality=10)
    assert r.last_quality == 5
    r = scheduler.review("mem1", quality=-1)
    assert r.last_quality == 0


def test_easiness_never_below_minimum():
    scheduler = _make_scheduler()
    scheduler.add_memory("mem1", "test")
    for _ in range(10):
        scheduler.review("mem1", quality=0)
    m = scheduler._load_all()["mem1"]
    assert m.easiness >= MIN_EASINESS


# ── Quick review ──

def test_quick_review_remembered():
    scheduler = _make_scheduler()
    scheduler.add_memory("mem1", "test memory")
    m = scheduler.quick_review("mem1", remembered=True)
    assert m is not None
    assert m.last_quality == 4
    assert m.repetitions == 1


def test_quick_review_forgotten():
    scheduler = _make_scheduler()
    scheduler.add_memory("mem1", "test memory")
    m = scheduler.quick_review("mem1", remembered=False)
    assert m is not None
    assert m.last_quality == 2
    assert m.repetitions == 0


def test_quick_review_nonexistent():
    scheduler = _make_scheduler()
    assert scheduler.quick_review("nonexistent", remembered=True) is None


# ── Due memories ──

def test_get_due_memories():
    scheduler = _make_scheduler()
    # Add a memory with past due date
    now = datetime.now(timezone.utc)
    m = ScheduledMemory(memory_id="mem1", content="due memory",
                        next_review_at=(now - timedelta(hours=1)).isoformat())
    scheduler._save_all({"mem1": m})
    due = scheduler.get_due_memories()
    assert len(due) == 1
    assert due[0].memory_id == "mem1"


def test_get_due_memories_empty():
    scheduler = _make_scheduler()
    # Add a memory with future next_review
    now = datetime.now(timezone.utc)
    m = ScheduledMemory(memory_id="future", content="not due",
                        next_review_at=(now + timedelta(days=30)).isoformat())
    scheduler._save_all({"future": m})
    due = scheduler.get_due_memories()
    assert len(due) == 0


# ── Daily review ──

def test_daily_review_empty():
    scheduler = _make_scheduler()
    review = scheduler.get_daily_review()
    assert review["count"] == 0
    assert review["due_reviews"]["count"] == 0


def test_daily_review_with_memories():
    scheduler = _make_scheduler()
    now = datetime.now(timezone.utc)
    m = ScheduledMemory(memory_id="mem1", content="test memory",
                        next_review_at=(now - timedelta(hours=1)).isoformat())
    scheduler._save_all({"mem1": m})
    review = scheduler.get_daily_review()
    assert review["count"] >= 1
    assert len(review["items"]) >= 1
    # Structure verification
    assert "yesterday_summary" in review
    assert "this_week_highlights" in review
    assert "due_reviews" in review
    assert "generated_at" in review


# ── Get by creation date ──

def test_get_new_memories():
    scheduler = _make_scheduler()
    m = scheduler.add_memory("mem1", "recent memory")
    recent = scheduler.get_new_memories(days=7)
    assert len(recent) == 1
    assert recent[0].memory_id == "mem1"


def test_get_by_creation_date():
    scheduler = _make_scheduler()
    m = scheduler.add_memory("mem1", "today memory")
    today = datetime.now(timezone.utc).date()
    result = scheduler.get_by_creation_date(today)
    assert len(result) == 1
    assert result[0].memory_id == "mem1"


# ── On This Day ──

def test_on_this_day_empty():
    scheduler = _make_scheduler()
    result = scheduler.get_on_this_day()
    assert result["total"] == 0
    assert result["timeline"] == []


def test_on_this_day_with_past_memory():
    scheduler = _make_scheduler()
    now = datetime.now(timezone.utc)
    # Simulate a memory from the same month/day but last year
    past_date = now.replace(year=now.year - 1)
    m = ScheduledMemory(
        memory_id="past1",
        content="last year memory",
        created_at=past_date.isoformat(),
    )
    scheduler._save_all({"past1": m})
    result = scheduler.get_on_this_day()
    assert result["total"] >= 1
    assert len(result["timeline"]) >= 1


# ── Stats ──

def test_get_stats_empty():
    scheduler = _make_scheduler()
    stats = scheduler.get_stats()
    assert stats["total"] == 0
    assert stats["due_today"] == 0
    assert stats["consolidated_count"] == 0
    assert stats["pending_count"] == 0
    assert stats["review_streak"] == 0


def test_get_stats_with_memories():
    scheduler = _make_scheduler()
    scheduler.add_memory("mem1", "test 1")
    scheduler.add_memory("mem2", "test 2")
    stats = scheduler.get_stats()
    assert stats["total"] == 2
    assert stats["avg_easiness"] == DEFAULT_EASINESS
    assert stats["pending_count"] == 2  # both never reviewed


def test_get_stats_consolidated():
    scheduler = _make_scheduler()
    scheduler.add_memory("mem1", "test")
    # Reach consolidated state: 4 passes
    for q in [4, 4, 4]:
        scheduler.review("mem1", quality=q)
    # repetitions=3 -> consolidated but not yet 4th (that needs 4 passes: rep0→1, rep1→2, rep2→3)
    # Wait, repetitions=3 means 3 consecutive passes, which is >= 3, so consolidated
    stats = scheduler.get_stats()
    assert stats["consolidated_count"] == 1


# ── Review streak ──

def test_review_streak():
    scheduler = _make_scheduler()
    m = scheduler.add_memory("mem1", "test")
    # Manually set last_reviewed_at to today
    m.last_reviewed_at = datetime.now(timezone.utc).isoformat()
    scheduler._save_all({"mem1": m})
    streak = scheduler._calculate_review_streak()
    assert streak >= 1


# ── Review settings ──

def test_save_load_settings():
    scheduler = _make_scheduler()
    settings = ReviewSettings(review_hour=7, daily_max_items=20, quiet_days=[5, 6])
    scheduler.save_settings(settings)
    loaded = scheduler.load_settings()
    assert loaded.review_hour == 7
    assert loaded.daily_max_items == 20
    assert loaded.quiet_days == [5, 6]


def test_quiet_days():
    scheduler = _make_scheduler()
    settings = ReviewSettings(quiet_days=[])
    assert settings.is_quiet_today() is False


# ── Review event ──

def test_review_event():
    scheduler = _make_scheduler()
    now = datetime.now(timezone.utc)
    m = ScheduledMemory(memory_id="mem1", content="test memory",
                        next_review_at=(now - timedelta(hours=1)).isoformat())
    scheduler._save_all({"mem1": m})
    event = scheduler.to_review_event()
    assert event["event_type"] == "daily_review_ready"
    assert "review" in event
    assert "stats" in event
    assert event["review"]["due_count"] >= 1


# ── Custom max interval ──

def test_custom_max_interval():
    scheduler = MemoryScheduler(data_store=_FakeDataStore(), max_interval_days=30)
    assert scheduler.max_interval_days == 30


# ── Storage isolation ──

def test_storage_key_isolation():
    scheduler = _make_scheduler()
    assert scheduler._storage_key == "assistant_memory_schedules"


# ── Memory type categorization ──

def test_daily_review_categories():
    scheduler = _make_scheduler()
    now = datetime.now(timezone.utc)
    memories = {
        "mem1": ScheduledMemory(memory_id="mem1", content="factual", memory_type="factual",
                                next_review_at=(now - timedelta(hours=1)).isoformat()),
        "mem2": ScheduledMemory(memory_id="mem2", content="pref", memory_type="preference",
                                next_review_at=(now - timedelta(hours=1)).isoformat()),
    }
    scheduler._save_all(memories)
    review = scheduler.get_daily_review()
    cats = review.get("categories", {})
    assert isinstance(cats, dict)
    assert sum(cats.values()) == review["count"]


# ── P1-12: _today_cache 在 _save_all 后必须失效 ──

def test_today_cache_invalidated_after_save_all():
    """P1-12 回归：_save_all 后 _today_cache 必须重置，
    否则刚刚 add/review 的记忆会被旧缓存挡掉。
    """
    scheduler = _make_scheduler()
    now = datetime.now(timezone.utc)

    # 第一次查询，建立缓存（空）
    due0 = scheduler.get_due_memories()
    assert due0 == []
    assert scheduler._today_cache is not None  # 缓存已建立

    # 此时通过 _save_all 注入一条已到期记忆
    scheduler._save_all({
        "m1": ScheduledMemory(
            memory_id="m1",
            content="due now",
            next_review_at=(now - timedelta(hours=1)).isoformat(),
        )
    })
    assert scheduler._today_cache is None, "_save_all 应清空 _today_cache"

    # 再查应命中刚刚的记录，而不是返回旧的空列表
    due1 = scheduler.get_due_memories()
    assert len(due1) == 1
    assert due1[0].memory_id == "m1"


def test_review_invalidates_today_cache_via_save_all():
    """P1-12 回归：review() 通过 _save_all 间接清空缓存，
    保证下一次 get_due_memories 重新计算到期集合。
    """
    scheduler = _make_scheduler()
    now = datetime.now(timezone.utc)
    scheduler._save_all({
        "m1": ScheduledMemory(
            memory_id="m1",
            content="due",
            next_review_at=(now - timedelta(hours=1)).isoformat(),
        )
    })

    # 第一次查询，命中并缓存
    due_before = scheduler.get_due_memories()
    assert len(due_before) == 1
    assert scheduler._today_cache is not None

    # 用户复习并打高分，间隔会被推到 INITIAL_INTERVAL_DAYS（1 天）之后
    scheduler.review("m1", quality=5)
    assert scheduler._today_cache is None, "review() 应触发缓存失效"

    # 复习完成后，今日到期列表应变空（next_review_at 推到明天）
    due_after = scheduler.get_due_memories()
    assert due_after == []


def test_today_cache_invalidated_after_remove_memory():
    """P1-12 回归：remove_memory 通过 _save_all 清空缓存。"""
    scheduler = _make_scheduler()
    now = datetime.now(timezone.utc)
    scheduler._save_all({
        "m1": ScheduledMemory(
            memory_id="m1",
            content="due",
            next_review_at=(now - timedelta(hours=1)).isoformat(),
        )
    })

    due_before = scheduler.get_due_memories()
    assert len(due_before) == 1
    assert scheduler._today_cache is not None

    assert scheduler.remove_memory("m1") is True
    assert scheduler._today_cache is None

    due_after = scheduler.get_due_memories()
    assert due_after == []
