"""UserProfileService 单元测试 '"四层分层画像模型。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.user_profile import DEFAULT_PROFILE, UserProfileService
from data.local_storage import LocalDataStore
from repositories import LocalSettingsRepository


def _make_service(tmp_path, *, initial_settings=None):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    if initial_settings:
        for k, v in initial_settings.items():
            settings_repo.set_setting(k, v)
    return UserProfileService(data_store=data_store, settings_repository=settings_repo)


# 鈹€鈹€ 鍒濆化与加载 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_load_returns_default_profile_on_first_access(tmp_path):
    svc = _make_service(tmp_path)
    profile = svc.load()
    assert profile["schema_version"] == 1
    assert isinstance(profile["long_term"], dict)
    assert isinstance(profile["permission"], dict)
    assert isinstance(profile["output"], dict)
    assert profile["output"]["preferred_delivery"] == "inbox"


def test_load_persists_initial_profile(tmp_path):
    svc = _make_service(tmp_path)
    svc.load()
    profile2 = svc.load()
    assert profile2["schema_version"] == 1
    assert profile2["updated_at"] != ""


def test_load_migrates_focus_topics_from_settings(tmp_path):
    svc = _make_service(tmp_path, initial_settings={
        "assistant_focus_topics": ["AI", "Python"],
    })
    profile = svc.load()
    assert "AI" in profile["long_term"]["focus_topics"]
    assert "Python" in profile["long_term"]["focus_topics"]


# 鈹€鈹€ 层级更新 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_update_layer_long_term(tmp_path):
    svc = _make_service(tmp_path)
    profile = svc.update_layer("long_term", {"focus_topics": ["Rust", "Go"]})
    assert profile["long_term"]["focus_topics"] == ["Rust", "Go"]


def test_update_layer_output(tmp_path):
    svc = _make_service(tmp_path)
    profile = svc.update_layer("output", {"summary_style": "detailed"})
    assert profile["output"]["summary_style"] == "detailed"


def test_update_layer_rejects_unknown_layer(tmp_path):
    svc = _make_service(tmp_path)
    with pytest.raises(ValueError, match="unknown profile layer"):
        svc.update_layer("nonexistent", {"key": "val"})


def test_update_layer_preserves_other_fields(tmp_path):
    svc = _make_service(tmp_path)
    svc.update_layer("output", {"summary_style": "bullet"})
    profile = svc.update_layer("output", {"language": "en-US"})
    assert profile["output"]["summary_style"] == "bullet"
    assert profile["output"]["language"] == "en-US"


# 鈹€鈹€ 关注涓婚合并 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_merge_focus_topics_deduplicates(tmp_path):
    svc = _make_service(tmp_path)
    svc.merge_focus_topics(["AI", "Python"])
    profile = svc.merge_focus_topics(["AI", "Rust"])
    topics = profile["long_term"]["focus_topics"]
    assert topics.count("AI") == 1
    assert "Rust" in topics


def test_merge_focus_topics_max_12(tmp_path):
    svc = _make_service(tmp_path)
    profile = svc.merge_focus_topics([f"topic_{i}" for i in range(20)])
    assert len(profile["long_term"]["focus_topics"]) == 12


def test_merge_focus_topics_strips_whitespace(tmp_path):
    svc = _make_service(tmp_path)
    profile = svc.merge_focus_topics(["  AI  ", " ", ""])
    assert "AI" in profile["long_term"]["focus_topics"]
    # 空白条目不应进入
    for t in profile["long_term"]["focus_topics"]:
        assert t.strip() == t and t != ""


# 鈹€鈹€ 场景设置 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_set_current_focus(tmp_path):
    svc = _make_service(tmp_path)
    profile = svc.set_current_focus("鍐欒鏂", "毕业璁捐")
    assert profile["context"]["current_focus"] == "鍐欒鏂"
    assert profile["context"]["active_project"] == "毕业璁捐"
    assert profile["context"]["last_context_at"] != ""


# 鈹€鈹€ 安静鏃舵 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_set_quiet_hours(tmp_path):
    svc = _make_service(tmp_path)
    profile = svc.set_quiet_hours(["22:00-08:00"])
    assert profile["permission"]["quiet_hours"] == ["22:00-08:00"]


def test_is_in_quiet_hours_within_range(tmp_path):
    svc = _make_service(tmp_path)
    svc.set_quiet_hours(["22:00-08:00"])
    assert svc.is_in_quiet_hours(datetime(2026, 4, 19, 23, 30)) is True
    assert svc.is_in_quiet_hours(datetime(2026, 4, 19, 6, 0)) is True


def test_is_in_quiet_hours_outside_range(tmp_path):
    svc = _make_service(tmp_path)
    svc.set_quiet_hours(["22:00-08:00"])
    assert svc.is_in_quiet_hours(datetime(2026, 4, 19, 12, 0)) is False


def test_is_in_quiet_hours_same_day_range(tmp_path):
    svc = _make_service(tmp_path)
    svc.set_quiet_hours(["12:00-14:00"])
    assert svc.is_in_quiet_hours(datetime(2026, 4, 19, 13, 0)) is True
    assert svc.is_in_quiet_hours(datetime(2026, 4, 19, 15, 0)) is False


# 鈹€鈹€ save_settings_preferences 统一写入 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_save_settings_preferences_updates_multiple_layers(tmp_path):
    svc = _make_service(tmp_path)
    profile = svc.save_settings_preferences(
        focus_topics=["AI", "鑷姩鍖", "AI"],
        common_sources=["36姘", "TechCrunch", "36姘"],
        current_focus="写周鎶",
        active_project="思忆闆",
        quiet_hours=["22:00-08:00", " 13:00-14:00 "],
        allow_proactive_push=False,
        preferred_delivery="both",
        summary_style="bullet",
    )
    assert profile["long_term"]["focus_topics"] == ["AI", "鑷姩鍖"]
    assert profile["long_term"]["common_sources"] == ["36姘", "TechCrunch"]
    assert profile["context"]["current_focus"] == "写周鎶"
    assert profile["context"]["active_project"] == "思忆闆"
    assert profile["permission"]["quiet_hours"] == ["22:00-08:00", "13:00-14:00"]
    assert profile["permission"]["allow_proactive_push"] is False
    assert profile["output"]["preferred_delivery"] == "both"
    assert profile["output"]["summary_style"] == "bullet"


def test_save_settings_preferences_partial_update_preserves_others(tmp_path):
    svc = _make_service(tmp_path)
    svc.save_settings_preferences(focus_topics=["AI", "ML"], summary_style="bullet")
    profile = svc.save_settings_preferences(preferred_delivery="notification")
    assert profile["long_term"]["focus_topics"] == ["AI", "ML"]
    assert profile["output"]["summary_style"] == "bullet"
    assert profile["output"]["preferred_delivery"] == "notification"


def test_save_settings_preferences_validates_quiet_hours(tmp_path):
    svc = _make_service(tmp_path)
    with pytest.raises(ValueError, match="invalid quiet hours format"):
        svc.save_settings_preferences(quiet_hours=["22:00~08:00"])


def test_save_settings_preferences_deduplicates_sources(tmp_path):
    svc = _make_service(tmp_path)
    profile = svc.save_settings_preferences(
        common_sources=["36姘", "36姘", "TechCrunch"],
    )
    assert profile["long_term"]["common_sources"] == ["36姘", "TechCrunch"]


def test_save_settings_preferences_caps_sources_at_20(tmp_path):
    svc = _make_service(tmp_path)
    profile = svc.save_settings_preferences(
        common_sources=[f"source_{i}" for i in range(30)],
    )
    assert len(profile["long_term"]["common_sources"]) == 20


# 鈹€鈹€ 重置 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_reset_profile_restores_defaults(tmp_path):
    svc = _make_service(tmp_path)
    svc.save_settings_preferences(
        focus_topics=["AI"],
        current_focus="冲刺",
        quiet_hours=["22:00-08:00"],
        allow_proactive_push=False,
        preferred_delivery="both",
        summary_style="bullet",
    )
    profile = svc.reset_profile()
    assert profile["long_term"]["focus_topics"] == DEFAULT_PROFILE["long_term"]["focus_topics"]
    assert profile["context"]["current_focus"] == DEFAULT_PROFILE["context"]["current_focus"]
    assert profile["permission"]["allow_proactive_push"] == DEFAULT_PROFILE["permission"]["allow_proactive_push"]
    assert profile["output"]["preferred_delivery"] == DEFAULT_PROFILE["output"]["preferred_delivery"]


# 鈹€鈹€ 推理辅助鏌ヨ 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_get_focus_topics_from_profile(tmp_path):
    svc = _make_service(tmp_path)
    svc.merge_focus_topics(["AI"])
    topics = svc.get_focus_topics()
    assert "AI" in topics
    assert len(topics) >= 1


def test_get_focus_topics_fallback_to_settings(tmp_path):
    svc = _make_service(tmp_path, initial_settings={
        "assistant_focus_topics": ["Fallback"],
    })
    profile = svc.load()
    profile["long_term"]["focus_topics"] = []
    svc.save(profile)
    assert svc.get_focus_topics() == ["Fallback"]


def test_get_summary_style(tmp_path):
    svc = _make_service(tmp_path)
    svc.update_layer("output", {"summary_style": "detailed"})
    assert svc.get_summary_style() == "detailed"


def test_get_preferred_delivery(tmp_path):
    svc = _make_service(tmp_path)
    svc.update_layer("output", {"preferred_delivery": "notification"})
    assert svc.get_preferred_delivery() == "notification"


def test_get_auto_execute_level(tmp_path):
    svc = _make_service(tmp_path)
    svc.update_layer("permission", {"auto_execute_level": "medium"})
    assert svc.get_auto_execute_level() == "medium"


def test_is_proactive_allowed_default_true(tmp_path):
    svc = _make_service(tmp_path)
    assert svc.is_proactive_allowed() is True


def test_is_proactive_allowed_can_disable(tmp_path):
    svc = _make_service(tmp_path)
    svc.update_layer("permission", {"allow_proactive_push": False})
    assert svc.is_proactive_allowed() is False


def test_get_proactive_categories(tmp_path):
    svc = _make_service(tmp_path)
    cats = svc.get_proactive_categories()
    assert "scheduled" in cats
    assert "anomaly" in cats


# 鈹€鈹€ Planner / Router 鎽樿 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_build_planner_summary_includes_topics(tmp_path):
    svc = _make_service(tmp_path)
    svc.merge_focus_topics(["AI", "安全"])
    svc.set_current_focus("写代鐮", "思忆闆")
    summary = svc.build_planner_summary()
    assert "AI" in summary
    assert "写代鐮" in summary
    assert "思忆闆" in summary


def test_build_planner_summary_empty_profile(tmp_path):
    svc = _make_service(tmp_path)
    summary = svc.build_planner_summary()
    assert isinstance(summary, str)


def test_build_planner_summary_includes_key_people_and_events(tmp_path):
    """个人知识体系 M2：重要人物 / 近期大事 / 画像更新进入对话锚点摘要。"""
    svc = _make_service(tmp_path)
    svc.merge_key_people(["张三（朋友）", "李四（同事）"])
    svc.merge_recent_events(["2024-01-01 周末爬山"])
    summary = svc.build_planner_summary()
    assert "张三（朋友）" in summary
    assert "李四（同事）" in summary
    assert "2024-01-01 周末爬山" in summary


def test_build_routing_context_structure(tmp_path):
    svc = _make_service(tmp_path)
    svc.merge_focus_topics(["AI"])
    ctx = svc.build_routing_context()
    assert "AI" in ctx["focus_topics"]
    assert ctx["summary_style"] == "concise"
    assert ctx["preferred_delivery"] == "inbox"
    assert ctx["allow_proactive"] is True
    assert isinstance(ctx["quiet_hours"], list)


# 鈹€鈹€ 偏好学习 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_observe_action_records_tool(tmp_path):
    svc = _make_service(tmp_path)
    svc.observe_action("web.search")
    svc.observe_action("notes.sync")
    profile = svc.load()
    tools = profile["long_term"]["preferred_tools"]
    assert "web.search" in tools
    assert "notes.sync" in tools


def test_observe_action_deduplicates(tmp_path):
    svc = _make_service(tmp_path)
    svc.observe_action("web.search")
    svc.observe_action("web.search")
    profile = svc.load()
    assert profile["long_term"]["preferred_tools"].count("web.search") == 1


def test_observe_action_max_20(tmp_path):
    svc = _make_service(tmp_path)
    for i in range(25):
        svc.observe_action(f"tool.{i}")
    profile = svc.load()
    assert len(profile["long_term"]["preferred_tools"]) <= 20


# 鈹€鈹€ Schema 补全 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def test_ensure_schema_fills_missing_fields(tmp_path):
    svc = _make_service(tmp_path)
    svc.data_store.save_json("assistant_user_profile", {
        "schema_version": 1,
        "updated_at": "",
        "long_term": {"focus_topics": ["AI"]},
    })
    profile = svc.load()
    assert "permission" in profile
    assert "allow_proactive_push" in profile["permission"]
    assert profile["long_term"]["focus_topics"] == ["AI"]
