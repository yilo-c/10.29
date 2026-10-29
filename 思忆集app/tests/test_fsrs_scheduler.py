"""Sprint 3A — FSRS scheduler shim 算法层单元测试。

不触及 repository / IO，纯函数/类输入输出。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from fsrs import Rating, State

from repositories._fsrs_scheduler import (
    quality_to_rating,
    review_with_dual_write,
    rollback_fsrs,
    _earlier_iso,
)


# ── quality 映射 ──

def test_quality_to_rating_mapping():
    assert quality_to_rating(0) == Rating.Again
    assert quality_to_rating(1) == Rating.Again
    assert quality_to_rating(2) == Rating.Again
    assert quality_to_rating(3) == Rating.Hard
    assert quality_to_rating(4) == Rating.Good
    assert quality_to_rating(5) == Rating.Easy


# ── _earlier_iso ──

def test_earlier_iso_picks_earlier():
    a = "2026-01-01T00:00:00+00:00"
    b = "2026-01-02T00:00:00+00:00"
    assert _earlier_iso(a, b) == a
    assert _earlier_iso(b, a) == a


def test_earlier_iso_handles_empty():
    a = "2026-01-01T00:00:00+00:00"
    assert _earlier_iso("", a) == a
    assert _earlier_iso(a, "") == a
    assert _earlier_iso("", "") == ""


# ── review_with_dual_write 双写 ──

def test_first_review_writes_both_field_sets():
    """全新词第一次评分：双写完整。"""
    word = {"id": 1, "word": "abandon"}
    now = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
    out = review_with_dual_write(word, quality=4, now=now)

    # SM-2 字段
    assert out["repetitions"] == 1
    assert out["interval"] == 1   # 第一次 good → 1 天
    assert out["last_review"] == now.isoformat()
    assert "next_review" in out

    # FSRS 字段
    assert out["fsrs_state"] in {int(s) for s in State}
    assert out["fsrs_stability"] is not None
    assert out["fsrs_difficulty"] is not None
    assert out["fsrs_due"]
    assert out["fsrs_enabled"] is True
    assert out["fsrs_last_rating"] == int(Rating.Good)


def test_quality_invalid_raises():
    word = {"id": 1, "word": "x"}
    with pytest.raises(ValueError):
        review_with_dual_write(word, quality=-1)
    with pytest.raises(ValueError):
        review_with_dual_write(word, quality=6)


def test_dual_write_does_not_mutate_input():
    word = {"id": 1, "word": "abandon"}
    snap = dict(word)
    review_with_dual_write(word, quality=4)
    assert word == snap


def test_repeat_review_advances_sm2():
    """连续两次 good：interval 1 → 6。"""
    word = {"id": 1, "word": "x"}
    now = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
    out1 = review_with_dual_write(word, quality=4, now=now)
    word2 = {**word, **out1}
    out2 = review_with_dual_write(word2, quality=4, now=now + timedelta(days=1))
    assert out1["repetitions"] == 1
    assert out2["repetitions"] == 2
    assert out2["interval"] == 6


def test_again_resets_sm2_repetitions():
    word = {
        "id": 1,
        "repetitions": 5,
        "efactor": 2.6,
        "interval": 30,
    }
    out = review_with_dual_write(word, quality=1)
    assert out["repetitions"] == 0
    assert out["interval"] == 1


def test_next_review_picks_earlier_of_two_schedules():
    """next_review 应取 SM-2 与 FSRS due 较早者。"""
    word = {"id": 1}
    now = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
    out = review_with_dual_write(word, quality=5, now=now)
    nr = datetime.fromisoformat(out["next_review"])
    sm2_nr = now + timedelta(days=int(out["interval"]))
    fsrs_due = datetime.fromisoformat(out["fsrs_due"])
    assert nr <= max(sm2_nr, fsrs_due)
    assert nr == min(sm2_nr, fsrs_due)


def test_legacy_word_promoted_to_review_state():
    """已有 SM-2 历史的词初次走 FSRS：FSRS 应处于 Review 状态。"""
    word = {
        "id": 1,
        "word": "established",
        "repetitions": 4,
        "efactor": 2.5,
        "interval": 15,
        "last_review": "2026-06-01T10:00:00+00:00",
        "next_review": "2026-06-16T10:00:00+00:00",
    }
    # 固定复习时刻为到期日：不传 now 会用真实当前时间，逾期越久
    # FSRS 的逾期增益越大，断言会随时间推移失效（时间炸弹）。
    out = review_with_dual_write(word, quality=4, now=datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc))
    assert out["fsrs_state"] == int(State.Review)
    # stability 应继承 interval（量级一致）
    assert 5 <= out["fsrs_stability"] <= 60


def test_subsequent_fsrs_reviews_use_persisted_state():
    """第二次评分应基于第一次 fsrs 状态计算（而不是从 SM-2 重新推算）。"""
    word = {"id": 1}
    now = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
    out1 = review_with_dual_write(word, quality=4, now=now)
    word2 = {**word, **out1}
    out2 = review_with_dual_write(word2, quality=4, now=now + timedelta(days=2))
    # FSRS due 应当在第二次 review 之后向后推进
    fsrs_due_2 = datetime.fromisoformat(out2["fsrs_due"])
    fsrs_due_1 = datetime.fromisoformat(out1["fsrs_due"])
    assert fsrs_due_2 > fsrs_due_1


def test_rollback_strips_fsrs_fields():
    word = {
        "id": 1,
        "word": "x",
        "repetitions": 3,
        "efactor": 2.5,
        "fsrs_state": 2,
        "fsrs_stability": 8.7,
        "fsrs_difficulty": 5.4,
        "fsrs_step": None,
        "fsrs_due": "2026-07-01T00:00:00+00:00",
        "fsrs_last_review": "2026-06-15T00:00:00+00:00",
        "fsrs_enabled": True,
        "fsrs_last_rating": 3,
    }
    cleaned = rollback_fsrs(word)
    assert cleaned["word"] == "x"
    assert cleaned["repetitions"] == 3
    assert cleaned["efactor"] == 2.5
    for k in ("fsrs_state", "fsrs_stability", "fsrs_difficulty", "fsrs_step",
              "fsrs_due", "fsrs_last_review", "fsrs_enabled", "fsrs_last_rating"):
        assert k not in cleaned
    # 不应修改原 dict
    assert "fsrs_state" in word
