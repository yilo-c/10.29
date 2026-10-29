"""Integration tests for translate.text and translate.languages RPC handlers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from _fake_settings import configured_settings_repository


class FakeContext:
    """Minimal context stub with a mock ai_client."""

    def __init__(self, ai_client=None):
        self.ai_client = ai_client
        # 2026-09-18：同上，改用回落 SETTINGS_DEFAULTS 真源的假仓储。
        self.settings_repository = configured_settings_repository()


@pytest.fixture
def mock_ai_client():
    client = MagicMock()
    client.complete_text.return_value = "你好世界"
    return client


@pytest.fixture
def ctx(mock_ai_client):
    return FakeContext(mock_ai_client)


def test_translate_text_empty(ctx):
    from backend.handlers._ai import register_ai
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_ai(registry, ctx)
    import asyncio

    result = asyncio.new_event_loop().run_until_complete(registry.dispatch("translate.text", {"text": "", "source": "auto", "target": "zh"}))
    assert result["translated"] == ""


def test_translate_text_basic(ctx, mock_ai_client):
    from backend.handlers._ai import register_ai
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_ai(registry, ctx)
    import asyncio

    result = asyncio.new_event_loop().run_until_complete(registry.dispatch("translate.text", {"text": "hello", "source": "en", "target": "zh"}))
    assert result["original"] == "hello"
    assert result["translated"] == "你好世界"
    assert result["source"] == "en"
    assert result["target"] == "zh"
    mock_ai_client.complete_text.assert_called_once()


def test_translate_text_ai_failure(ctx, mock_ai_client):
    mock_ai_client.complete_text.side_effect = RuntimeError("API down")

    from backend.handlers._ai import register_ai
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_ai(registry, ctx)
    import asyncio

    result = asyncio.new_event_loop().run_until_complete(registry.dispatch("translate.text", {"text": "hello", "source": "auto", "target": "zh"}))
    assert result["original"] == "hello"
    assert "error" in result


def test_translate_languages(ctx):
    from backend.handlers._ai import register_ai
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_ai(registry, ctx)
    import asyncio

    result = asyncio.new_event_loop().run_until_complete(registry.dispatch("translate.languages", {}))
    assert isinstance(result, list)
    codes = {lang["code"] for lang in result}
    assert "zh" in codes
    assert "en" in codes
    assert "ja" in codes


def test_translate_large_text(ctx, mock_ai_client):
    mock_ai_client.complete_text.return_value = "译" * 3000

    from backend.handlers._ai import register_ai
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_ai(registry, ctx)
    import asyncio

    result = asyncio.new_event_loop().run_until_complete(registry.dispatch("translate.text", {"text": "x" * 5000, "source": "auto", "target": "zh"}))
    assert result["original"] == "x" * 5000
    assert len(result["translated"]) > 0


def test_translate_code_preservation(ctx, mock_ai_client):
    """Verify the translate prompt includes code preservation instructions."""
    mock_ai_client.complete_text.return_value = "翻译结果"

    from backend.handlers._ai import register_ai
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_ai(registry, ctx)
    import asyncio

    asyncio.new_event_loop().run_until_complete(registry.dispatch("translate.text", {"text": "print('hello')", "source": "auto", "target": "zh"}))
    # The prompt sent to AI should include the code-preservation instruction
    call_args = mock_ai_client.complete_text.call_args
    messages = call_args[0][0]
    prompt_text = messages[0]["content"]
    assert "代码" in prompt_text or "code" in prompt_text.lower()
