from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.local_router import (
    CONFIDENCE_AUTO,
    CONFIDENCE_SUGGEST,
    LocalAIRouter,
    LocalProcessingResult,
)


def _make_settings(**overrides) -> SimpleNamespace:
    defaults = {
        "llm_local_api_base": "http://localhost:11434",
        "llm_local_model": "qwen2.5:1.5b",
    }
    merged = {**defaults, **overrides}

    class FakeSettings:
        def get_setting(self, key, default=""):
            return merged.get(key, default)

    return FakeSettings()


def test_router_returns_empty_on_empty_input():
    router = LocalAIRouter(ai_client=None)
    result = router.process("")
    assert result.handled is False
    assert result.confidence == 0.0
    assert result.reason == "empty_input"


def test_router_escalates_when_local_unavailable():
    router = LocalAIRouter(ai_client=None)
    result = router.process("写一段 Python 代码")
    assert result.needs_judge is True
    assert result.handled is False


def test_local_processing_result_defaults():
    r = LocalProcessingResult(
        handled=True, confidence=0.9, task_type="chat", response="ok"
    )
    assert r.handled is True
    assert r.confidence == 0.9
    assert r.task_type == "chat"
    assert r.response == "ok"
    assert r.needs_judge is False
    assert r.judge_context == {}
    assert r.reason == ""


def test_local_processing_result_auto_escalation():
    r = LocalProcessingResult(
        handled=False,
        confidence=0.3,
        task_type="complex",
        needs_judge=True,
        judge_context={"input": "test"},
        reason="too_complex",
    )
    assert r.needs_judge is True
    assert r.judge_context == {"input": "test"}


def test_router_uses_local_settings_when_available():
    settings = _make_settings()
    router = LocalAIRouter(ai_client=SimpleNamespace(local=None), settings_repository=settings)
    status = router.get_status_snapshot()
    assert "local_available" in status
