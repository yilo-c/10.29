# -*- coding: utf-8 -*-
"""FeedbackService（services/feedback_service.py）纯逻辑单测。

TASK-V2-03 覆盖率棘轮：services.feedback_service 此前 0%，
本文件用 fake data_store / settings / ai 端口全路径覆盖：
- record_feedback / list_feedback / get_stats / delete_feedback
- get/set_preference / count_learning_samples
- aggregate（below_threshold / force / no_samples / AI 提炼 / 规则兜底）
- _rule_summarize / _ai_summarize（含端口正常、端口异常、无端口兼容路径）
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services.feedback_service import (
    DEFAULT_AGGREGATE_THRESHOLD,
    MAX_FEEDBACK_RECORDS,
    MAX_PREFERENCE_LEN,
    FeedbackService,
)


class FakeStore:
    """最小 data_store：load_json / save_json 读写字典。"""

    def __init__(self) -> None:
        self.data: dict = {}

    def load_json(self, key: str, default=None):
        return self.data.get(key, default)

    def save_json(self, key: str, value) -> None:
        self.data[key] = value


class FakeSettings:
    def __init__(self, threshold: int | None = None) -> None:
        self.threshold = threshold

    def get_setting(self, key: str, default=None):
        return default if self.threshold is None else self.threshold


class _EventRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def emit(self, event, payload=None) -> None:
        # 兼容新契约（单参 AppEvent）与旧契约（双参 name, payload）
        if hasattr(event, "event_type") and hasattr(event, "payload"):
            self.events.append((event.event_type, event.payload))
        else:
            self.events.append((event, payload))


def _make_service(*, store=None, settings=None, ai=None, ai_service=None, bus=None) -> FeedbackService:
    return FeedbackService(
        data_store=store or FakeStore(),
        settings_repository=settings or FakeSettings(),
        ai_client=ai,
        event_bus=bus,
        ai_text_service=ai_service,
    )


# ── record_feedback ─────────────────────────────────────────────

def test_record_feedback_up():
    svc = _make_service()
    out = svc.record_feedback(session_id="s1", rating="up")
    assert out["recorded"] is True
    fb = out["feedback"]
    assert fb["rating"] == "up"
    assert fb["id"].startswith("fb_")
    assert fb["is_edited"] is False
    # 持久化
    assert svc._store.load_json("chat_feedback") == [fb]


def test_record_feedback_down_with_edited():
    svc = _make_service()
    svc.record_feedback(rating="down", original_user_msg="hello", original_ai_msg="world", edited_text="new")
    recs = svc._store.load_json("chat_feedback")
    assert recs[0]["rating"] == "down"
    assert recs[0]["is_edited"] is True


def test_record_feedback_edited_same_as_original_is_not_edited():
    svc = _make_service()
    svc.record_feedback(rating="down", original_ai_msg="world", edited_text="world")
    assert svc._store.load_json("chat_feedback")[0]["is_edited"] is False


def test_record_feedback_invalid_rating_raises():
    svc = _make_service()
    with pytest.raises(ValueError):
        svc.record_feedback(rating="sideways")


def test_record_feedback_lru_trims_over_max():
    svc = _make_service()
    for i in range(MAX_FEEDBACK_RECORDS + 5):
        svc.record_feedback(rating="up", session_id=f"u{i}")
    recs = svc._store.load_json("chat_feedback")
    assert len(recs) == MAX_FEEDBACK_RECORDS
    # 最早写入的已丢弃
    assert all(r["session_id"] != "u0" for r in recs)


def test_record_feedback_emits_event_on_bus():
    bus = _EventRecorder()
    svc = _make_service(bus=bus)
    svc.record_feedback(rating="up")
    assert bus.events and bus.events[0][0] == "feedback.recorded"


def test_record_feedback_bus_emit_exception_swallowed():
    class Boom:
        def emit(self, *a, **k):
            raise RuntimeError("bus down")

    svc = _make_service(bus=Boom())
    out = svc.record_feedback(rating="up")
    assert out["recorded"] is True


# ── list_feedback ───────────────────────────────────────────────

def test_list_feedback_newest_first():
    svc = _make_service()
    svc.record_feedback(rating="up", session_id="a")
    svc.record_feedback(rating="down", session_id="b")
    lst = svc.list_feedback()
    assert lst[0]["session_id"] == "b"  # 最新在前


def test_list_feedback_filters_session_and_rating():
    svc = _make_service()
    for s, r in [("a", "up"), ("a", "down"), ("b", "up")]:
        svc.record_feedback(rating=r, session_id=s)
    assert len(svc.list_feedback(session_id="a")) == 2
    assert len(svc.list_feedback(rating="up")) == 2
    assert len(svc.list_feedback(session_id="a", rating="up")) == 1


def test_list_feedback_limit_clamped():
    svc = _make_service()
    for i in range(30):
        svc.record_feedback(rating="up", session_id=f"u{i}")
    assert len(svc.list_feedback(limit=5)) == 5
    assert len(svc.list_feedback(limit=10000)) <= 500


def test_list_feedback_non_list_store_returns_empty():
    store = FakeStore()
    store.data["chat_feedback"] = "not-a-list"
    svc = _make_service(store=store)
    assert svc.list_feedback() == []


# ── get_stats ───────────────────────────────────────────────────

def test_get_stats_counts_and_daily():
    svc = _make_service()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    svc.record_feedback(rating="up", session_id="a")
    svc.record_feedback(rating="down", session_id="b")
    svc.record_feedback(rating="down", session_id="c", original_ai_msg="x", edited_text="y")
    stats = svc.get_stats(days=7)
    assert stats["total"] == 3
    assert stats["up"] == 1
    assert stats["down"] == 2
    assert stats["edited"] == 1
    assert stats["learning_samples"] == 2  # down×1(down 计) + c down+edited(计1)
    assert stats["aggregate_threshold"] == DEFAULT_AGGREGATE_THRESHOLD
    assert stats["days"] == 7
    assert stats["daily"][today] == 3


def test_get_stats_days_clamped_and_positional():
    svc = _make_service()
    stats = svc.get_stats(0)  # 位置参数 + 非法值 → clamp 到 1
    assert stats["days"] == 1
    stats2 = svc.get_stats(days=10000)
    assert stats2["days"] == 365


def test_get_stats_threshold_from_settings():
    svc = _make_service(settings=FakeSettings(threshold=9))
    assert svc.get_stats()["aggregate_threshold"] == 9


# ── delete_feedback ─────────────────────────────────────────────

def test_delete_feedback_removes_by_id():
    svc = _make_service()
    fb = svc.record_feedback(rating="up")["feedback"]
    assert svc.delete_feedback(fb["id"]) is True
    assert svc._store.load_json("chat_feedback") == []


def test_delete_feedback_missing_id_returns_false():
    svc = _make_service()
    svc.record_feedback(rating="up")
    assert svc.delete_feedback("nope") is False


def test_delete_feedback_non_list_store_returns_false():
    store = FakeStore()
    store.data["chat_feedback"] = "bad"
    svc = _make_service(store=store)
    assert svc.delete_feedback("x") is False


# ── preference ──────────────────────────────────────────────────

def test_preference_roundtrip_and_truncate():
    svc = _make_service()
    assert svc.get_preference() == ""
    long_summary = "x" * (MAX_PREFERENCE_LEN + 50)
    svc.set_preference(long_summary)
    stored = svc.get_preference()
    assert len(stored) == MAX_PREFERENCE_LEN


def test_get_preference_non_str_returns_empty():
    store = FakeStore()
    store.data["chat_feedback_preference"] = 123
    svc = _make_service(store=store)
    assert svc.get_preference() == ""


# ── count_learning_samples ──────────────────────────────────────

def test_count_learning_samples():
    svc = _make_service()
    svc.record_feedback(rating="up", session_id="a")           # 不计
    svc.record_feedback(rating="down", session_id="b")         # 计
    svc.record_feedback(rating="up", session_id="c", original_ai_msg="x", edited_text="y")  # 计
    assert svc.count_learning_samples() == 2


# ── aggregate ───────────────────────────────────────────────────

def test_aggregate_below_threshold():
    svc = _make_service()
    svc.record_feedback(rating="up", session_id="a")
    out = asyncio.run(svc.aggregate())
    assert out["aggregated"] is False
    assert out["reason"] == "below_threshold"


def test_aggregate_force_no_samples():
    svc = _make_service()  # 无任何样本
    out = asyncio.run(svc.aggregate(force=True))
    assert out["aggregated"] is False
    assert out["reason"] == "no_samples"


def test_aggregate_rule_fallback_without_ai():
    svc = _make_service()  # ai=None → 规则兜底
    for _ in range(DEFAULT_AGGREGATE_THRESHOLD):
        svc.record_feedback(rating="down", original_ai_msg="x", edited_text="fix")
    out = asyncio.run(svc.aggregate())
    assert out["aggregated"] is True
    assert "用户会主动改写回复" in out["summary"]


def test_aggregate_uses_ai_service_port():
    class AiService:
        def complete(self, user_prompt: str):
            return {"choices": [{"message": {"content": "偏好：保持简洁"}}]}

    svc = _make_service(ai=object(), ai_service=AiService())
    for _ in range(DEFAULT_AGGREGATE_THRESHOLD):
        svc.record_feedback(rating="down")
    out = asyncio.run(svc.aggregate())
    assert out["summary"] == "偏好：保持简洁"
    assert svc.get_preference() == "偏好：保持简洁"


def test_aggregate_ai_service_port_raises_falls_to_rule():
    class BadAiService:
        def complete(self, user_prompt: str):
            raise RuntimeError("ai down")

    svc = _make_service(ai=object(), ai_service=BadAiService())
    for _ in range(DEFAULT_AGGREGATE_THRESHOLD):
        svc.record_feedback(rating="down", original_ai_msg="x", edited_text="y")
    out = asyncio.run(svc.aggregate())
    assert out["aggregated"] is True
    assert out["summary"]  # 规则兜底非空


def test_aggregate_compat_chat_completion_path():
    class CompatAI:
        def chat_completion(self, messages):
            return {"choices": [{"message": {"content": "规则A"}}]}

    svc = _make_service(ai=CompatAI(), ai_service=None)
    for _ in range(DEFAULT_AGGREGATE_THRESHOLD):
        svc.record_feedback(rating="down")
    out = asyncio.run(svc.aggregate())
    assert out["aggregated"] is True


def test_aggregate_compat_complete_text_path():
    class TextAI:
        def complete_text(self, prompt):
            return "全文偏好"

    svc = _make_service(ai=TextAI())
    for _ in range(DEFAULT_AGGREGATE_THRESHOLD):
        svc.record_feedback(rating="down")
    out = asyncio.run(svc.aggregate())
    assert out["aggregated"] is True


def test_aggregate_emits_aggregated_event():
    bus = _EventRecorder()
    svc = _make_service(bus=bus)
    for _ in range(DEFAULT_AGGREGATE_THRESHOLD):
        svc.record_feedback(rating="down", original_ai_msg="x", edited_text="y")
    asyncio.run(svc.aggregate())
    assert any(e[0] == "feedback.aggregated" for e in bus.events)


# ── _rule_summarize（规则兜底纯函数） ─────────────────────────

def test_rule_summarize_down_dominant():
    svc = _make_service()
    samples = [{"rating": "down", "is_edited": False}, {"rating": "down", "is_edited": False}]
    text = svc._rule_summarize(samples)
    assert "用户对部分回复不满意" in text
    assert "保持简洁" in text


def test_rule_summarize_edited_present():
    svc = _make_service()
    samples = [{"rating": "up", "is_edited": True}]
    text = svc._rule_summarize(samples)
    assert "用户会主动改写回复" in text