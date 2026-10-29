from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.character_runtime import (
    AssistantToolRegistry,
    AssistantToolSpec,
    BrowserAssistantEventPolicy,
    canonical_subtitle_tone,
    map_assistant_tone_to_mood,
)


def test_tool_registry_can_register_local_actions_and_match_keywords():
    registry = AssistantToolRegistry()
    registry.register_local_actions(
        [
            (
                "tasks",
                ("打开任务", "进入任务", "查看任务"),
                "已打开任务窗口。",
                "任务窗口",
            )
        ]
    )

    matches = registry.match_tools("请帮我打开任务", limit=3)

    assert matches
    assert matches[0].tool_id == "local.tasks"
    assert matches[0].requires_confirmation is True


def test_tool_registry_can_register_capability_catalog():
    registry = AssistantToolRegistry()
    registry.register_capability_catalog(
        [
            {
                "action_id": "notes.sync",
                "description": "笔记双向同步",
                "risk": "medium",
                "payload_schema": {"mode": "bidirectional"},
            }
        ]
    )

    tools = registry.list_tools()

    assert len(tools) == 1
    assert tools[0].tool_id == "notes.sync"
    assert tools[0].requires_confirmation is True
    assert "mode" in tools[0].description


def test_tool_registry_preview_labels_preserve_registered_specs():
    registry = AssistantToolRegistry()
    registry.register_batch(
        [
            AssistantToolSpec(tool_id="local.capture", label="动态收件", description="收件到动态收件箱"),
            AssistantToolSpec(tool_id="local.organize_current", label="整理当前", description="整理为私人空间笔记"),
        ]
    )

    labels = registry.preview_labels(limit=2)

    assert labels == ["动态收件", "整理当前"]


def test_browser_event_policy_can_route_task_intent_from_summary_keywords():
    route, confidence = BrowserAssistantEventPolicy.classify_route(
        "browser.summarized",
        {
            "title": "发布说明",
            "summary": "本周需要执行上线任务并修复关键 bug",
            "url": "https://example.com/release",
        },
    )

    assert route == BrowserAssistantEventPolicy.ROUTE_TASK
    assert confidence >= 50


def test_browser_event_policy_builds_role_subtitle_for_note_event():
    subtitle, tone, route = BrowserAssistantEventPolicy.build_subtitle(
        "browser.note_created",
        {"title": "学习笔记：桥接架构"},
    )

    assert "笔记" in subtitle
    assert tone == "proud"
    assert route == BrowserAssistantEventPolicy.ROUTE_NOTE


def test_tone_canonicalization_and_mood_mapping_support_extended_aliases():
    assert canonical_subtitle_tone("planning") == "thinking"
    assert canonical_subtitle_tone("warning") == "alert"
    assert map_assistant_tone_to_mood("proud") == "happy"
    assert map_assistant_tone_to_mood("focus") == "thinking"


def test_browser_event_policy_prefers_recommended_action_in_subtitle():
    subtitle, tone, route = BrowserAssistantEventPolicy.build_subtitle(
        "browser.summarized",
        {
            "title": "项目规范手册",
            "summary": "这是一个偏文档型的总结。",
            "recommendedActions": [
                {
                    "actionId": "createNote",
                    "label": "优先转成笔记",
                    "reason": "文档页更适合保留目录和知识点结构。",
                    "priority": "100",
                    "enabled": "true",
                }
            ],
        },
    )

    assert "优先转成笔记" in subtitle
    assert tone == "planning"
    assert route == BrowserAssistantEventPolicy.ROUTE_INBOX
