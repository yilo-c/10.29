"""单元测试：对话记忆锚点 (P3.5)。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.handlers._chat_context_anchor import (
    build_chat_context_anchor,
    is_anchor_enabled,
    _format_local_time,
)


def test_format_local_time_zh_zh():
    text = _format_local_time(datetime(2026, 6, 23, 15, 30))
    # 2026-06-23 是星期二
    assert "下午" in text
    assert "15:30" in text
    assert "星期二" in text
    assert "2026-06-23" in text


def test_format_local_time_morning():
    text = _format_local_time(datetime(2026, 6, 23, 8, 0))
    assert "上午" in text


def test_format_local_time_late_night():
    text = _format_local_time(datetime(2026, 6, 23, 23, 45))
    assert "晚上" in text


def test_format_local_time_early_morning():
    text = _format_local_time(datetime(2026, 6, 23, 4, 0))
    assert "凌晨" in text


def _empty_ctx():
    ctx = SimpleNamespace()
    ctx.memory_index_service = None
    ctx.user_profile_service = None
    ctx.weather_service = None
    ctx.settings_repository = None
    return ctx


def test_anchor_with_only_time():
    """所有服务缺失：仍输出时间锚点。"""
    ctx = _empty_ctx()
    text = build_chat_context_anchor(ctx, "今天写点什么", now=datetime(2026, 6, 23, 15, 30))
    assert text.startswith("[对话锚点]")
    assert "现在：" in text
    assert "星期二" in text


def test_anchor_empty_when_no_data_and_no_message():
    """无 message + 无服务 → 仍有时间锚点（时间总会有）。"""
    ctx = _empty_ctx()
    text = build_chat_context_anchor(ctx, "", now=datetime(2026, 6, 23, 10, 0))
    assert "[对话锚点]" in text
    assert "现在：" in text


def test_anchor_includes_memories():
    ctx = _empty_ctx()
    mem = MagicMock()
    mem.find_related.return_value = [
        {"title": "Go 项目笔记", "summary": "迁移到 1.22", "source_type": "note"},
        {"title": "周末待办", "snippet": "买菜", "source_type": "task"},
    ]
    ctx.memory_index_service = mem
    text = build_chat_context_anchor(ctx, "Go 1.22 升级", now=datetime(2026, 6, 23, 9, 0))
    assert "相关记忆：" in text
    assert "Go 项目笔记" in text
    assert "周末待办" in text
    assert "[note]" in text
    mem.find_related.assert_called_once_with("Go 1.22 升级", limit=4)


def test_anchor_memory_failure_is_silent():
    ctx = _empty_ctx()
    mem = MagicMock()
    mem.find_related.side_effect = RuntimeError("boom")
    ctx.memory_index_service = mem
    text = build_chat_context_anchor(ctx, "anything", now=datetime(2026, 6, 23, 9, 0))
    # 不抛异常；时间锚点仍在
    assert "[对话锚点]" in text
    assert "相关记忆" not in text


def test_anchor_includes_weather():
    ctx = _empty_ctx()
    weather = MagicMock()
    weather.get_snapshot.return_value = {
        "city_label": "杭州",
        "condition": "多云",
        "temperature": 28.0,
        "tip": "适合外出",
    }
    ctx.weather_service = weather
    text = build_chat_context_anchor(ctx, "hi", now=datetime(2026, 6, 23, 12, 0))
    assert "天气：杭州" in text
    assert "多云" in text
    assert "28℃" in text
    assert "适合外出" in text


def test_anchor_includes_user_profile():
    ctx = _empty_ctx()
    profile = MagicMock()
    profile.build_planner_summary.return_value = "用户偏好：编程、阅读\n常用时段：晚上"
    ctx.user_profile_service = profile
    text = build_chat_context_anchor(ctx, "hi", now=datetime(2026, 6, 23, 21, 0))
    assert "用户画像：" in text
    assert "用户偏好：编程、阅读" in text
    assert "常用时段：晚上" in text


def test_anchor_truncates_long_profile():
    ctx = _empty_ctx()
    profile = MagicMock()
    profile.build_planner_summary.return_value = "x" * 1000
    ctx.user_profile_service = profile
    text = build_chat_context_anchor(ctx, "hi", now=datetime(2026, 6, 23, 21, 0))
    assert "..." in text


def test_is_anchor_enabled_default_true():
    ctx = _empty_ctx()
    assert is_anchor_enabled(ctx) is True


def test_is_anchor_enabled_override_false():
    ctx = _empty_ctx()
    assert is_anchor_enabled(ctx, override=False) is False


def test_is_anchor_enabled_setting_false():
    ctx = _empty_ctx()
    repo = MagicMock()
    repo.get_setting.return_value = False
    ctx.settings_repository = repo
    assert is_anchor_enabled(ctx) is False
    repo.get_setting.assert_called_once_with("chat_context_anchor", True)


def test_is_anchor_enabled_settings_failure_defaults_true():
    ctx = _empty_ctx()
    repo = MagicMock()
    repo.get_setting.side_effect = RuntimeError("settings error")
    ctx.settings_repository = repo
    assert is_anchor_enabled(ctx) is True


def test_anchor_combines_all_layers():
    ctx = _empty_ctx()
    mem = MagicMock()
    mem.find_related.return_value = [{"title": "T", "summary": "S", "source_type": "note"}]
    ctx.memory_index_service = mem
    weather = MagicMock()
    weather.get_snapshot.return_value = {"city": "北京", "condition": "晴", "temperature": 30}
    ctx.weather_service = weather
    profile = MagicMock()
    profile.build_planner_summary.return_value = "喜欢 Go"
    ctx.user_profile_service = profile

    text = build_chat_context_anchor(ctx, "go 学习", now=datetime(2026, 6, 23, 14, 30))
    assert "[对话锚点]" in text
    assert "现在：" in text
    assert "天气：北京" in text
    assert "相关记忆：" in text
    assert "用户画像：" in text
    assert "喜欢 Go" in text
