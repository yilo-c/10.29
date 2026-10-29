from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# 直接 import 子包真实模块：monkeypatch 需要修改的是真实模块的全局命名空间，
# 而非 compat 层 re-export 的副本（compat 层 `import *` 复制绑定，patch 打不到子包模块）。
import assistant.routing.intent_router as intent_router_module
from assistant.routing.intent_router import INTENT_AUTOMATE, INTENT_CHAT, INTENT_EXECUTE, AssistantIntentRouter


def test_intent_router_classifies_automate_request():
    router = AssistantIntentRouter()

    result = router.classify("每天早上帮我发一条 AI 简报")

    assert result.intent == INTENT_AUTOMATE
    assert result.confidence >= 0.7


def test_intent_router_classifies_execute_request():
    router = AssistantIntentRouter()

    result = router.classify("帮我搜一下最新 AI 新闻")

    assert result.intent == INTENT_EXECUTE
    assert result.confidence >= 0.6


def test_intent_router_classifies_chat_request():
    router = AssistantIntentRouter()

    result = router.classify("你好，什么是 transformer")

    assert result.intent == INTENT_CHAT
    assert result.confidence >= 0.5


def test_intent_router_defaults_empty_input_to_chat():
    router = AssistantIntentRouter()

    result = router.classify("")

    assert result.intent == INTENT_CHAT
    assert result.confidence == 0.5


# ── Phase 6: 画像感知路由 ────────────────────────────────
from types import SimpleNamespace


def _make_fake_profile_service(*, quiet_hours=None, allow_proactive=True,
                                auto_execute_level="low"):
    """构造 stub profile service，仅实现 build_routing_context。"""
    ctx = {
        "focus_topics": [],
        "common_sources": [],
        "current_focus": "",
        "active_project": "",
        "summary_style": "concise",
        "preferred_delivery": "inbox",
        "auto_execute_level": auto_execute_level,
        "allow_proactive": allow_proactive,
        "quiet_hours": quiet_hours or [],
    }
    return SimpleNamespace(build_routing_context=lambda: ctx)


def test_route_includes_profile_context():
    """route() 返回的字典应包含 profile_context。"""
    svc = _make_fake_profile_service()
    router = AssistantIntentRouter(user_profile_service=svc)

    result = router.route("你好")

    assert "profile_context" in result
    assert result["profile_context"]["auto_execute_level"] == "low"


def test_route_quiet_hours_suppresses_proactive(monkeypatch):
    """当前时间在 quiet_hours 区间内，route() 应标记 quiet_suppressed=True。"""
    svc = _make_fake_profile_service(quiet_hours=["00:00-23:59"])
    router = AssistantIntentRouter(user_profile_service=svc)

    result = router.route("每天早上帮我发简报")

    assert result.get("quiet_suppressed") is True


def test_route_quiet_hours_outside_range_not_suppressed(monkeypatch):
    """当前时间不在 quiet_hours 区间，不标记 quiet_suppressed。"""
    # 使用一个不可能命中的时间段
    svc = _make_fake_profile_service(quiet_hours=["25:00-25:01"])
    router = AssistantIntentRouter(user_profile_service=svc)

    result = router.route("每天早上帮我发简报")

    assert result.get("quiet_suppressed") is not True


def test_route_auto_execute_level_none_downgrades_execute():
    """auto_execute_level='none' 时，execute 意图应被降级为 chat。"""
    svc = _make_fake_profile_service(auto_execute_level="none")
    router = AssistantIntentRouter(user_profile_service=svc)

    result = router.route("帮我搜一下最新 AI 新闻")

    cls = result["classification"]
    assert cls["intent"] == INTENT_CHAT
    assert "downgraded" in cls.get("reason", "")


class _FakeAiClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_classify_by_llm_includes_profile_summary_in_system_prompt():
    svc = _make_fake_profile_service(auto_execute_level="medium")
    svc.build_routing_context = lambda: {
        "focus_topics": ["AI", "软件工程"],
        "current_focus": "写日报",
        "active_project": "思忆集",
        "preferred_delivery": "chat_output",
        "auto_execute_level": "medium",
        "quiet_hours": [],
    }
    client = _FakeAiClient(
        {
            "choices": [
                {
                    "message": {
                        "content": '{"intent": "execute", "confidence": 0.91, "reason": "llm"}'
                    }
                }
            ]
        }
    )
    router = AssistantIntentRouter(ai_client=client, user_profile_service=svc)

    result = router.classify("帮我处理一下")

    assert result.intent == INTENT_EXECUTE
    system_prompt = client.calls[0]["messages"][0]["content"]
    assert "用户画像摘要" in system_prompt
    assert "当前关注: 写日报" in system_prompt
    assert "活跃项目: 思忆集" in system_prompt
    assert "关注主题: AI, 软件工程" in system_prompt


def test_classify_uses_profile_focus_to_bias_ambiguous_execute_request():
    svc = _make_fake_profile_service()
    svc.build_routing_context = lambda: {
        "focus_topics": ["AI", "自动化"],
        "current_focus": "",
        "active_project": "",
        "preferred_delivery": "inbox",
        "auto_execute_level": "low",
        "quiet_hours": [],
    }
    router = AssistantIntentRouter(user_profile_service=svc)

    result = router.classify("看下 AI")

    assert result.intent == INTENT_EXECUTE
    assert result.reason == "profile_focus_bias"


def test_in_quiet_hours_handles_cross_midnight_range(monkeypatch):
    class _LateNight:
        @classmethod
        def now(cls):
            class _Now:
                hour = 0
                minute = 30
            return _Now()

    monkeypatch.setattr(intent_router_module, "datetime", _LateNight)

    assert AssistantIntentRouter._in_quiet_hours(["23:00-01:00"]) is True


def test_in_quiet_hours_handles_cross_midnight_outside_range(monkeypatch):
    class _Morning:
        @classmethod
        def now(cls):
            class _Now:
                hour = 10
                minute = 0
            return _Now()

    monkeypatch.setattr(intent_router_module, "datetime", _Morning)

    assert AssistantIntentRouter._in_quiet_hours(["23:00-01:00"]) is False
