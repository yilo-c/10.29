"""个人画像自动提取调度器测试（个人知识体系 ①）。"""
from __future__ import annotations

import asyncio

import pytest

from backend import personal_profile_scheduler as sched


class FakeRepo:
    def __init__(self, items):
        self.items = items

    def list(self, *, processed=None, limit=100):
        return self.items

    def mark_processed_many(self, ids):
        return len(ids)


class FakeProfile:
    def load(self):
        return {}

    def merge_key_people(self, people):
        pass

    def merge_focus_topics(self, topics):
        pass

    def set_current_focus(self, focus):
        pass


class FakeContext:
    def __init__(self, items):
        self.raw_item_repo = FakeRepo(items)
        self.user_profile_service = FakeProfile()
        self.ai_client = None
        self.local_data_store = FakeStore()


class FakeStore:
    def __init__(self):
        self.saved: dict = {}

    def save_json(self, key, value):
        self.saved[key] = value

    def load_json(self, key, default=None):
        return self.saved.get(key, default)


def _reset():
    sched._scheduler_task = None
    sched._enabled = True
    sched._schedule_time = "02:00"
    sched._last_run_date = None
    sched._context_ref = None


class TestParse:
    def test_parse_valid(self):
        assert sched._parse_hhmm("07:30") == (7, 30)
        assert sched._parse_hhmm("02:00") == (2, 0)

    def test_parse_invalid_defaults(self):
        assert sched._parse_hhmm("bad") == (2, 0)
        assert sched._parse_hhmm("") == (2, 0)


class TestShouldRun:
    def _patch_now(self, monkeypatch, date: str, hour: int, minute: int):
        class _FakeDatetime:
            @staticmethod
            def now():
                return _FakeNow(date, hour, minute)
        monkeypatch.setattr(sched, "datetime", _FakeDatetime)

    def test_not_run_after_scheduled_time_today(self, monkeypatch):
        _reset()
        sched._last_run_date = "2026-08-17"
        self._patch_now(monkeypatch, "2026-08-17", 10, 0)
        assert sched._should_run() is False

    def test_run_when_new_day_past_time(self, monkeypatch):
        _reset()
        sched._last_run_date = "2026-08-16"
        self._patch_now(monkeypatch, "2026-08-17", 10, 0)
        assert sched._should_run() is True

    def test_not_run_before_time(self, monkeypatch):
        _reset()
        sched._last_run_date = None
        sched._schedule_time = "02:00"
        self._patch_now(monkeypatch, "2026-08-17", 1, 0)
        assert sched._should_run() is False


class _FakeNow:
    def __init__(self, date: str, hour: int, minute: int):
        self._d = date
        self._h = hour
        self._m = minute

    def strftime(self, fmt):
        if fmt == "%Y-%m-%d":
            return self._d
        return ""

    @property
    def hour(self):
        return self._h

    @property
    def minute(self):
        return self._m


class TestRunOnce:
    def test_run_once_extracts_and_persists(self):
        _reset()
        sched.set_context(FakeContext([
            {"id": "a1", "content": "我喜欢爬山", "source": "wechat", "meta": {"sender": "张三"}, "processed": 0},
            {"id": "a2", "content": "周末去爬山", "source": "wechat", "meta": {"sender": "李四"}, "processed": 0},
        ]))
        result = asyncio.run(sched.run_once())
        assert result["success"] is True
        assert result["scanned"] == 2
        assert result["processed"] == 2
        assert sched._last_run_date is not None

    def test_run_once_missing_repo(self):
        _reset()
        sched.set_context(FakeContext([]))
        sched._context_ref.raw_item_repo = None
        result = asyncio.run(sched.run_once())
        assert result["success"] is False

    def test_run_once_no_context(self):
        _reset()
        result = asyncio.run(sched.run_once())
        assert result["success"] is False


class TestConfigure:
    def test_configure_toggles(self):
        _reset()
        cfg = sched.configure(enabled=False)
        assert cfg == {"success": True, "enabled": False, "time": "02:00"}
        assert sched._enabled is False

    def test_configure_time(self):
        _reset()
        cfg = sched.configure(enabled=True, time="03:30")
        assert cfg["time"] == "03:30"
        assert sched._schedule_time == "03:30"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
