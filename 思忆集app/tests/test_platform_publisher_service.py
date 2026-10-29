"""Tests for PlatformPublisherService."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.platform_publisher_service import PlatformPublisherService, SUPPORTED_PLATFORMS


class _FakeSettings:
    def __init__(self, data: dict[str, str] | None = None):
        self._data = data or {}

    def get(self, key: str) -> str:
        return self._data.get(key, "")


@pytest.mark.asyncio
async def test_publish_unsupported_platform():
    svc = PlatformPublisherService()
    result = await svc.publish(platform="unknown", content={"title": "T", "body": "B"})
    assert result["success"] is False
    assert "不支持" in result.get("error", "")


@pytest.mark.asyncio
async def test_publish_empty_title_or_body():
    svc = PlatformPublisherService()
    result = await svc.publish(platform="wordpress", content={"title": "", "body": ""})
    assert result["success"] is False
    assert "不能为空" in result.get("error", "")


@pytest.mark.asyncio
async def test_publish_wordpress_no_credentials():
    svc = PlatformPublisherService(settings_repository=_FakeSettings())
    result = await svc.publish(platform="wordpress", content={"title": "T", "body": "B"})
    assert result["success"] is False
    assert "凭证" in result.get("error", "")


@pytest.mark.asyncio
async def test_publish_medium_no_token():
    svc = PlatformPublisherService(settings_repository=_FakeSettings())
    result = await svc.publish(platform="medium", content={"title": "T", "body": "B"})
    assert result["success"] is False
    assert "token" in result.get("error", "").lower() or "未配置" in result.get("error", "")


@pytest.mark.asyncio
async def test_publish_wordpress_partial_credentials():
    svc = PlatformPublisherService(settings_repository=_FakeSettings({
        "wordpress_url": "https://example.com",
    }))
    result = await svc.publish(platform="wordpress", content={"title": "T", "body": "B"})
    assert result["success"] is False
    assert "凭证" in result.get("error", "")


@pytest.mark.asyncio
async def test_supported_platforms():
    assert "wordpress" in SUPPORTED_PLATFORMS
    assert "medium" in SUPPORTED_PLATFORMS
    assert len(SUPPORTED_PLATFORMS) == 2


@pytest.mark.asyncio
async def test_publish_case_insensitive_platform():
    svc = PlatformPublisherService()
    result = await svc.publish(platform="WordPress", content={"title": "", "body": ""})
    # Platform is normalized, so this should not fail with "不支持"
    # It will fail with empty content instead
    assert "不支持" not in result.get("error", "")
