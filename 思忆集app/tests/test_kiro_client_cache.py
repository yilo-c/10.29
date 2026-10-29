"""P3-2 回归：create_kiro_client 的 TTL 缓存。

之前 JudgeAwareClient 每次路由到 Kiro 都会触发 ``requests.get('/health')``
+ 重建 ``OpenAICompatibleClient``，造成请求放大。修复后同 base/key 下
60s 内复用客户端，失败结果也缓存避免风暴。

迁移到 httpx 后，健康探测改为 ``httpx.get``。
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import httpx
import pytest

from ai.client import config as kiro_config


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """每条用例清空缓存 + 用默认 TTL。"""
    monkeypatch.delenv("SIYI_KIRO_HEALTH_TTL", raising=False)
    kiro_config._reset_kiro_client_cache()
    yield
    kiro_config._reset_kiro_client_cache()


def _ok_response():
    resp = MagicMock()
    resp.status_code = 200
    return resp


def test_success_response_cached_across_calls() -> None:
    """探测成功后，TTL 内不再发起新 HTTP 请求。"""
    with patch("httpx.get", return_value=_ok_response()) as mocked_get:
        c1 = kiro_config.create_kiro_client()
        c2 = kiro_config.create_kiro_client()
        c3 = kiro_config.create_kiro_client()

        assert c1 is not None
        # 多次调用应返回同一个客户端实例
        assert c1 is c2 is c3
        # /health 只发起一次
        assert mocked_get.call_count == 1


def test_failure_status_is_cached() -> None:
    """非 200 响应也缓存为 None，避免每次都打健康检查。"""
    bad = MagicMock()
    bad.status_code = 503
    with patch("httpx.get", return_value=bad) as mocked_get:
        r1 = kiro_config.create_kiro_client()
        r2 = kiro_config.create_kiro_client()

        assert r1 is None
        assert r2 is None
        assert mocked_get.call_count == 1


def test_exception_is_cached() -> None:
    """RequestError 也缓存为 None。"""
    with patch(
        "httpx.get",
        side_effect=httpx.ConnectError("kiro down"),
    ) as mocked_get:
        r1 = kiro_config.create_kiro_client()
        r2 = kiro_config.create_kiro_client()

        assert r1 is None
        assert r2 is None
        assert mocked_get.call_count == 1


def test_ttl_zero_disables_cache(monkeypatch) -> None:
    """``SIYI_KIRO_HEALTH_TTL=0`` 禁用缓存，每次都重新探测。"""
    monkeypatch.setenv("SIYI_KIRO_HEALTH_TTL", "0")
    with patch("httpx.get", return_value=_ok_response()) as mocked_get:
        kiro_config.create_kiro_client()
        kiro_config.create_kiro_client()
        kiro_config.create_kiro_client()

        assert mocked_get.call_count == 3


def test_reset_cache_helper() -> None:
    """``_reset_kiro_client_cache`` 清空缓存后重新探测。"""
    with patch("httpx.get", return_value=_ok_response()) as mocked_get:
        kiro_config.create_kiro_client()
        kiro_config._reset_kiro_client_cache()
        kiro_config.create_kiro_client()

        assert mocked_get.call_count == 2


def test_invalid_ttl_env_falls_back_to_default(monkeypatch) -> None:
    """非法 TTL 字符串应回退到 60s，不应抛异常。"""
    monkeypatch.setenv("SIYI_KIRO_HEALTH_TTL", "not-a-number")
    with patch("httpx.get", return_value=_ok_response()) as mocked_get:
        c1 = kiro_config.create_kiro_client()
        c2 = kiro_config.create_kiro_client()

        assert c1 is c2
        assert mocked_get.call_count == 1


def test_different_base_uses_separate_cache(monkeypatch) -> None:
    """不同 base/key 走独立缓存槽。"""
    with patch("httpx.get", return_value=_ok_response()) as mocked_get:
        monkeypatch.setenv("SIYI_KIRO_API_BASE", "http://127.0.0.1:8000")
        c1 = kiro_config.create_kiro_client()

        monkeypatch.setenv("SIYI_KIRO_API_BASE", "http://127.0.0.1:9000")
        c2 = kiro_config.create_kiro_client()

        assert c1 is not None and c2 is not None
        assert c1 is not c2
        assert mocked_get.call_count == 2
