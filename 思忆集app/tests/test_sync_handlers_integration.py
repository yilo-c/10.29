"""sync.* JSON-RPC handler 集成测试。

验证 backend/handlers/_sync.py 的 register_sync 正确注册 6 个方法，
且 dispatch 能正确路由到对应处理器。

这与 test_sync_bridge.py 互补：后者测试 SyncBridge 类本身，
本文件测试 handler → bridge 的接线层。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.method_registry import MethodRegistry
from backend.handlers._sync import register_sync


def _make_context(*, has_bridge=True, bridge_status=None, settings_data=None):
    ctx = SimpleNamespace()
    ctx.settings_repository = _make_settings_repo(settings_data or {})

    if has_bridge:
        bridge = MagicMock()
        bridge.client = MagicMock() if bridge_status else None
        bridge.get_status = MagicMock(return_value=bridge_status or {
            "enabled": False, "configured": False, "running": False,
            "message": "not started",
        })
        bridge.start = AsyncMock(return_value=True)
        bridge.stop = AsyncMock(return_value=None)
        bridge.build_client = MagicMock(return_value=MagicMock())
        ctx.cloud_sync_bridge = bridge
    else:
        ctx.cloud_sync_bridge = None

    return ctx


def _make_settings_repo(data=None):
    data = dict(data or {})
    repo = SimpleNamespace()
    repo._data = data
    repo.get_setting = lambda key, default=None: repo._data.get(key, default)
    repo.set_setting = lambda key, value: repo._data.__setitem__(key, value)
    return repo


# ── 注册完整性 ──

def test_register_sync_registers_all_6_methods():
    registry = MethodRegistry()
    ctx = _make_context()
    register_sync(registry, ctx)

    methods = registry.list_methods()
    expected = {
        "sync.status", "sync.sync_now", "sync.configure",
        "sync.enable", "sync.disable", "sync.register_device",
    }
    assert expected.issubset(set(methods)), f"missing: {expected - set(methods)}"


def test_register_sync_idempotent_overwrite():
    """重复注册不应报错（register 会 overwrite）。"""
    registry = MethodRegistry()
    ctx = _make_context()
    register_sync(registry, ctx)
    register_sync(registry, ctx)  # 不应抛异常
    methods = registry.list_methods()
    assert methods.count("sync.status") == 1


# ── sync.status dispatch ──

@pytest.mark.asyncio
async def test_sync_status_dispatch_with_bridge():
    registry = MethodRegistry()
    status = {"enabled": True, "configured": True, "running": True, "device_id": "dev1"}
    ctx = _make_context(bridge_status=status)
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.status", {})
    assert result["enabled"] is True
    assert result["running"] is True
    assert result["device_id"] == "dev1"


@pytest.mark.asyncio
async def test_sync_status_dispatch_without_bridge():
    registry = MethodRegistry()
    ctx = _make_context(has_bridge=False)
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.status", {})
    assert result["enabled"] is False
    assert result["configured"] is False
    assert result["running"] is False
    assert "message" in result


# ── sync.sync_now dispatch ──

@pytest.mark.asyncio
async def test_sync_now_dispatch_success():
    registry = MethodRegistry()
    ctx = _make_context(bridge_status={"enabled": True, "running": True})
    ctx.cloud_sync_bridge.client.sync_once = AsyncMock(return_value=True)
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.sync_now", {})
    assert result["success"] is True
    assert "status" in result


@pytest.mark.asyncio
async def test_sync_now_dispatch_no_client():
    registry = MethodRegistry()
    ctx = _make_context(bridge_status=None)  # client is None
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.sync_now", {})
    assert result["success"] is False
    assert "not started" in result["error"]


# ── sync.configure dispatch ──

@pytest.mark.asyncio
async def test_sync_configure_dispatch_updates_settings():
    registry = MethodRegistry()
    ctx = _make_context()
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.configure", {
        "worker_url": "https://sync.example.workers.dev",
        "token": "my-token",
        "register_secret": "reg-secret",
        "interval_seconds": 600,
    })
    assert result["success"] is True
    assert result["worker_url"] == "https://sync.example.workers.dev"
    assert result["has_token"] is True
    assert result["has_register_secret"] is True
    assert result["interval_seconds"] == 600


@pytest.mark.asyncio
async def test_sync_configure_dispatch_partial_update():
    registry = MethodRegistry()
    ctx = _make_context(settings_data={
        "cloud_sync_worker_url": "https://old.example.com",
        "cloud_sync_token": "old-token",
    })
    register_sync(registry, ctx)

    # 只更新 interval，其他保持不变
    result = await registry.dispatch("sync.configure", {"interval_seconds": 120})
    assert result["success"] is True
    assert result["worker_url"] == "https://old.example.com"
    assert result["has_token"] is True
    assert result["interval_seconds"] == 120


@pytest.mark.asyncio
async def test_sync_configure_dispatch_strips_trailing_slash():
    registry = MethodRegistry()
    ctx = _make_context()
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.configure", {
        "worker_url": "https://sync.example.workers.dev/",
    })
    assert result["worker_url"] == "https://sync.example.workers.dev"


# ── sync.enable dispatch ──

@pytest.mark.asyncio
async def test_sync_enable_dispatch_success():
    registry = MethodRegistry()
    ctx = _make_context(settings_data={
        "cloud_sync_worker_url": "https://sync.example.workers.dev",
        "cloud_sync_token": "tok",
    })
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.enable", {})
    assert result["success"] is True
    assert "status" in result


@pytest.mark.asyncio
async def test_sync_enable_dispatch_missing_config():
    registry = MethodRegistry()
    ctx = _make_context()  # 无 url/token
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.enable", {})
    assert result["success"] is False
    assert "worker_url and token" in result["error"]


# ── sync.disable dispatch ──

@pytest.mark.asyncio
async def test_sync_disable_dispatch_success():
    registry = MethodRegistry()
    ctx = _make_context()
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.disable", {})
    assert result["success"] is True
    assert result["enabled"] is False
    ctx.cloud_sync_bridge.stop.assert_awaited_once()


# ── sync.register_device dispatch ──

@pytest.mark.asyncio
async def test_sync_register_device_dispatch_success():
    registry = MethodRegistry()
    ctx = _make_context(
        bridge_status={"enabled": True, "running": True},
        settings_data={"cloud_sync_register_secret": "reg-secret"},
    )
    ctx.cloud_sync_bridge.client.register_device = AsyncMock(return_value=True)
    ctx.cloud_sync_bridge.client.device_id = "desktop-test"
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.register_device", {"name": "My PC"})
    assert result["success"] is True
    assert result["device_id"] == "desktop-test"


@pytest.mark.asyncio
async def test_sync_register_device_dispatch_no_secret():
    registry = MethodRegistry()
    ctx = _make_context()  # 无 register_secret
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.register_device", {"name": "My PC"})
    assert result["success"] is False
    assert "register_secret" in result["error"]


@pytest.mark.asyncio
async def test_sync_register_device_dispatch_no_bridge():
    registry = MethodRegistry()
    ctx = _make_context(has_bridge=False)
    register_sync(registry, ctx)

    result = await registry.dispatch("sync.register_device", {"name": "My PC"})
    assert result["success"] is False
    assert "not initialized" in result["error"]


# ── 方法不存在 ──

@pytest.mark.asyncio
async def test_dispatch_unknown_sync_method_raises():
    from backend.method_registry import MethodNotFound
    registry = MethodRegistry()
    ctx = _make_context()
    register_sync(registry, ctx)

    with pytest.raises(MethodNotFound):
        await registry.dispatch("sync.nonexistent", {})
