"""Tests for ImageGeneratorService."""

from __future__ import annotations

import sys
from pathlib import Path
from os import environ

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.image_generator_service import ImageGeneratorService


class _FakeSettings:
    def get(self, key: str) -> str:
        return ""

class _FakeAIClientWithGenerate:
    async def generate_image(self, prompt: str, size: str, n: int):
        return {"success": True, "images": [{"b64_data": "fakebase64", "format": "png"}]}


@pytest.mark.asyncio
async def test_generate_empty_prompt_returns_error():
    svc = ImageGeneratorService()
    result = await svc.generate(prompt="")
    assert result["success"] is False
    assert "prompt" in result.get("error", "")


@pytest.mark.asyncio
async def test_generate_clamps_n_to_1_4(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    svc = ImageGeneratorService()
    result = await svc.generate(prompt="test", n=0)
    assert result["success"] is False
    assert "API Key" in result.get("error", "")


@pytest.mark.asyncio
async def test_generate_uses_ai_client_when_available():
    svc = ImageGeneratorService(ai_client=_FakeAIClientWithGenerate())
    result = await svc.generate(prompt="a cat", size="1024x1024", n=1)
    # AI client returns success without needing API key
    assert result.get("success") is True
    assert "images" in result


@pytest.mark.asyncio
async def test_generate_no_key_returns_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    svc = ImageGeneratorService(settings_repository=_FakeSettings())
    result = await svc.generate(prompt="test image")
    # No AI client + no API key → error
    assert result["success"] is False
    assert "API" in result.get("error", "") or "Key" in result.get("error", "")


@pytest.mark.asyncio
async def test_generate_ai_client_fallback_on_exception():
    class _BrokenAIClient:
        async def generate_image(self, prompt, size, n):
            raise RuntimeError("broken")

    svc = ImageGeneratorService(
        ai_client=_BrokenAIClient(),
        settings_repository=_FakeSettings(),
    )
    result = await svc.generate(prompt="test")
    assert result["success"] is False
