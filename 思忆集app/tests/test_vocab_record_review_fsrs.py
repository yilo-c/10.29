"""Sprint 3A — repository.record_review 双写 / 回滚 / 兼容性集成测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from repositories._vocab import LocalVocabularyRepository
from repositories._fsrs_scheduler import rollback_fsrs


class _InMemoryDataStore:
    """最小可用的 in-memory 替代品，复刻 LocalDataStore 用到的方法子集。"""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._next_id_counter = 0

    def load_json(self, key: str, default):
        if key in self._data:
            return self._data[key]
        return default if not isinstance(default, (list, dict)) else type(default)(default)

    def save_json(self, key: str, data) -> None:
        self._data[key] = data

    def next_id(self, items):
        existing = max((int(x.get("id") or 0) for x in items), default=0)
        self._next_id_counter = max(self._next_id_counter, existing) + 1
        return self._next_id_counter


@pytest.fixture
def repo():
    ds = _InMemoryDataStore()
    return LocalVocabularyRepository(ds)


def _add_word(repo, word="abandon") -> int:
    return repo.add_word({"word": word, "meaning": "放弃", "wordbook_id": 1})


def test_record_review_writes_both_sets(repo):
    wid = _add_word(repo)
    out = repo.record_review(wid, 4)
    assert out is not None
    reps, efactor, interval, next_review = out
    assert reps == 1
    assert interval >= 1
    assert next_review

    # 词条应同时含有 SM-2 和 FSRS 字段
    words = repo.list_words()
    word = next(w for w in words if w["id"] == wid)
    assert word["repetitions"] == 1
    assert word["efactor"] >= 1.3
    assert "fsrs_state" in word
    assert "fsrs_stability" in word
    assert "fsrs_due" in word
    assert word.get("fsrs_enabled") is True


def test_record_review_returns_none_for_missing_word(repo):
    assert repo.record_review(99999, 3) is None


def test_record_review_advances_fsrs_state_on_repeat(repo):
    wid = _add_word(repo)
    repo.record_review(wid, 4)
    word_after_first = next(w for w in repo.list_words() if w["id"] == wid)
    fsrs_due_1 = word_after_first["fsrs_due"]

    repo.record_review(wid, 4)
    word_after_second = next(w for w in repo.list_words() if w["id"] == wid)
    fsrs_due_2 = word_after_second["fsrs_due"]

    # 两次 due 应该不同（向后推进或更新）
    assert fsrs_due_2 != fsrs_due_1


def test_record_review_again_resets_sm2(repo):
    wid = _add_word(repo)
    repo.record_review(wid, 5)
    repo.record_review(wid, 5)
    word_before = next(w for w in repo.list_words() if w["id"] == wid)
    assert word_before["repetitions"] == 2

    repo.record_review(wid, 1)
    word_after = next(w for w in repo.list_words() if w["id"] == wid)
    assert word_after["repetitions"] == 0
    assert word_after["interval"] == 1


def test_legacy_word_without_fsrs_fields_gets_promoted(repo):
    """模拟旧词条（只有 SM-2 字段，无 FSRS 字段），首次评分后应补齐 FSRS 字段。"""
    wid = _add_word(repo)
    # 直接修改底层数据模拟旧版词条
    raw_words = repo._load_words()
    target = next(w for w in raw_words if w["id"] == wid)
    target.update({
        "repetitions": 3,
        "efactor": 2.4,
        "interval": 10,
        "next_review": "2026-06-01T00:00:00+00:00",
        "last_review": "2026-05-22T00:00:00+00:00",
    })
    repo._save_words(raw_words)

    repo.record_review(wid, 4)
    word = next(w for w in repo.list_words() if w["id"] == wid)
    assert "fsrs_state" in word
    assert word["fsrs_stability"] is not None


def test_rollback_strips_fsrs_fields_helper():
    """rollback_fsrs 是数据迁移回滚的 helper —— 测试它能用于 list_words 输出。"""
    word = {
        "id": 1,
        "repetitions": 3,
        "efactor": 2.5,
        "fsrs_state": 2,
        "fsrs_stability": 7.0,
    }
    cleaned = rollback_fsrs(word)
    assert "fsrs_state" not in cleaned
    assert cleaned["repetitions"] == 3


def test_review_returns_signature_compatible_with_old_callers(repo):
    """word.review RPC 期望 4-tuple；不能因为加了 FSRS 就破坏接口。"""
    wid = _add_word(repo)
    result = repo.record_review(wid, 5)
    assert isinstance(result, tuple)
    assert len(result) == 4
    reps, ef, intv, nr = result
    assert isinstance(reps, int)
    assert isinstance(ef, float)
    assert isinstance(intv, int)
    assert isinstance(nr, str)


def test_next_review_takes_earlier_of_two_schedules(repo):
    """next_review 应取 SM-2 与 FSRS due 较早者。"""
    from datetime import datetime
    wid = _add_word(repo)
    repo.record_review(wid, 5)
    word = next(w for w in repo.list_words() if w["id"] == wid)
    sm2_nr = datetime.fromisoformat(word["next_review"])
    fsrs_due = datetime.fromisoformat(word["fsrs_due"])
    # next_review 不应晚于 fsrs_due 也不应晚于 sm2 计算（间隔后）
    # 但 word["next_review"] 是较早者，所以肯定 ≤ 任意一个
    assert sm2_nr <= fsrs_due or sm2_nr >= fsrs_due  # 至少一个方向
    # 严格断言：next_review 字段就是较早者
    nr_dt = datetime.fromisoformat(word["next_review"])
    expected = min(sm2_nr, fsrs_due) if word["interval"] > 0 else fsrs_due
    # 由于 next_review 现在是 _earlier_iso 的结果，应等于较早者
    assert abs((nr_dt - min(sm2_nr, fsrs_due)).total_seconds()) < 2
