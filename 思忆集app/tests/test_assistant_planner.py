from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.agents.planner import AssistantPlannerLayer


class _ProfileService:
    def __init__(self, snapshot=None, error: Exception | None = None):
        self._snapshot = snapshot
        self._error = error

    def build_routing_context(self):
        if self._error is not None:
            raise self._error
        return self._snapshot


def test_planner_uses_user_profile_service_focus_topics_when_profile_argument_missing():
    planner = AssistantPlannerLayer(
        settings_repository=None,
        user_profile_service=_ProfileService({"focus_topics": ["AI", "自动化"]}),
    )

    steps = planner.plan_goal("帮我看今天新闻")

    news_step = next(step for step in steps if step.action_id == "news.lookup")
    assert news_step.payload["query"] == "AI OR 自动化"


def test_planner_falls_back_to_keyword_plan_when_user_profile_service_raises():
    planner = AssistantPlannerLayer(
        settings_repository=None,
        user_profile_service=_ProfileService(error=RuntimeError("boom")),
    )

    steps = planner.plan_goal("帮我看今天新闻")

    news_step = next(step for step in steps if step.action_id == "news.lookup")
    assert news_step.payload["query"] == "AI OR 软件工程"


def test_planner_ignores_non_dict_user_profile_snapshot():
    planner = AssistantPlannerLayer(
        settings_repository=None,
        user_profile_service=_ProfileService("not-a-dict"),
    )

    steps = planner.plan_goal("帮我看今天新闻")

    news_step = next(step for step in steps if step.action_id == "news.lookup")
    assert news_step.payload["query"] == "AI OR 软件工程"


def test_build_user_profile_summary_includes_focus_and_project_context():
    summary = AssistantPlannerLayer._build_user_profile_summary(
        {
            "current_focus": "写周报",
            "active_project": "思忆集",
            "focus_topics": ["AI", "自动化"],
            "preferred_delivery": "chat_output",
        }
    )

    assert "当前关注: 写周报" in summary
    assert "活跃项目: 思忆集" in summary
    assert "关注主题: AI, 自动化" in summary
    assert "默认交付: chat_output" in summary


def test_build_user_profile_summary_prefers_prebuilt_summary():
    summary = AssistantPlannerLayer._build_user_profile_summary(
        {
            "summary": "预构建摘要",
            "current_focus": "should be ignored",
        }
    )

    assert summary == "预构建摘要"


def test_build_user_profile_summary_ignores_non_list_fields():
    summary = AssistantPlannerLayer._build_user_profile_summary(
        {
            "focus_topics": "AI",
            "common_sources": "BBC",
            "quiet_hours": "22:00-08:00",
        }
    )

    assert summary == ""


def test_resolve_news_query_filters_unsafe_focus_topics():
    planner = AssistantPlannerLayer(settings_repository=None)

    query = planner._resolve_news_query(
        {
            "focus_topics": ["AI", "自动化", "x OR y", "bad;drop"],
        }
    )

    assert query == "AI OR 自动化"
