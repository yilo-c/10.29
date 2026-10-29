"""Tests for InboxClassifierService."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.inbox_classifier_service import InboxClassifierService, CONTENT_KINDS


class _FakeAIClient:
    def chat_completion(self, messages, temperature=0.1, max_tokens=200):
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"kind": "task", "confidence": 0.92, "reason": "包含待办事项关键词", "suggested_action": "suggest_convert"}',
                    }
                }
            ]
        }


class _BrokenAIClient:
    def chat_completion(self, messages, temperature=0.1, max_tokens=200):
        raise RuntimeError("API error")


@pytest.mark.asyncio
async def test_empty_content_returns_uncertain():
    svc = InboxClassifierService()
    result = await svc.classify(content="")
    assert result["kind"] == "uncertain"
    assert result["confidence"] == 0.0


@pytest.mark.asyncio
async def test_no_ai_fallback_keyword_task():
    svc = InboxClassifierService()
    result = await svc.classify(content="明天需要完成项目报告", title="待办事项")
    assert result["kind"] == "task"


@pytest.mark.asyncio
async def test_no_ai_fallback_keyword_reminder():
    svc = InboxClassifierService()
    result = await svc.classify(content="下周一上午10点开会")
    assert result["kind"] == "reminder"


@pytest.mark.asyncio
async def test_no_ai_fallback_keyword_question():
    svc = InboxClassifierService()
    result = await svc.classify(content="什么是量子计算？")
    assert result["kind"] == "question"


@pytest.mark.asyncio
async def test_no_ai_fallback_url_reference():
    svc = InboxClassifierService()
    result = await svc.classify(content="https://example.com 一个有用的链接")
    assert result["kind"] == "reference"


@pytest.mark.asyncio
async def test_no_ai_fallback_long_content_note():
    svc = InboxClassifierService()
    result = await svc.classify(content="a" * 60)
    assert result["kind"] == "note"


@pytest.mark.asyncio
async def test_ai_classification_used_when_available():
    svc = InboxClassifierService(ai_client=_FakeAIClient())
    result = await svc.classify(content="buy groceries", title="shopping list")
    assert result["kind"] == "task"
    assert result["confidence"] > 0.8


@pytest.mark.asyncio
async def test_ai_failure_falls_back_to_keyword():
    svc = InboxClassifierService(ai_client=_BrokenAIClient())
    result = await svc.classify(content="明天需要完成项目报告")
    assert result["kind"] == "task"  # keyword fallback


@pytest.mark.asyncio
async def test_content_kinds_defined():
    assert "note" in CONTENT_KINDS
    assert "task" in CONTENT_KINDS
    assert "reminder" in CONTENT_KINDS
    assert "question" in CONTENT_KINDS
    assert "reference" in CONTENT_KINDS
    assert "idea" in CONTENT_KINDS
    assert "uncertain" in CONTENT_KINDS
