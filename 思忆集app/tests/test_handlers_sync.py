"""Handler-level tests for backend.handlers._sync (sync.* JSON-RPC methods).

验证 sync.status / sync.sync_now / sync.configure / sync.enable / sync.disable /
sync.register_device 的接线正确性，使用 mock context + mock bridge。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.method_registry import MethodRegistry
from backend.handlers._sync import register_sync


class FakeSettings:
    """内存版 settings_repository，支持 get/set。"""

    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    def get_setting(self, key: str, default=None):
        return self._data.get(key, default)

    def set_setting(self, key: str, value) -> None:
        self._data[key] = value


class FakeBridge:
    """模拟 SyncBridge，仅暴露 handler 用到的方法。"""

    def __init__(self, *, client=None, status=None) -> None:
        self.client = client
        self._status = status or {
            "enabled": True,
            "configured": True,
            "running": True,
            "last_sync_at": 0,
        }
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.build_client = MagicMock(return_value=client)

    def get_status(self) -> dict:
        return dict(self._status)


@pytest.fixture
def sync_env():
    """构建 registry + context，注册 sync.* 方法。"""
    registry = MethodRegistry()
    settings = FakeSettings()
    context = MagicMock()
    context.settings_repository = settings
    context.cloud_sync_bridge = None
    register_sync(registry, context)
    return registry, context, settings


# ── sync.status ──


async def test_sync_status_no_bridge(sync_env):
    registry, context, _ = sync_env
    result = await registry.dispatch("sync.status", {})
    assert result["enabled"] is False
    assert result["configured"] is False
    assert "not initialized" in result["message"]


async def test_sync_status_with_bridge(sync_env):
    registry, context, _ = sync_env
    bridge = FakeBridge(status={"enabled": True, "configured": True, "running": True})
    context.cloud_sync_bridge = bridge
    result = await registry.dispatch("sync.status", {})
    assert result["enabled"] is True
    assert result["running"] is True


# ── sync.sync_now ──


async def test_sync_now_no_bridge(sync_env):
    registry, context, _ = sync_env
    result = await registry.dispatch("sync.sync_now", {})
    assert result["success"] is False
    assert "not started" in result["error"]


async def test_sync_now_success(sync_env):
    registry, context, _ = sync_env
    client = MagicMock()
    client.sync_once = AsyncMock(return_value=True)
    bridge = FakeBridge(client=client)
    context.cloud_sync_bridge = bridge
    result = await registry.dispatch("sync.sync_now", {})
    assert result["success"] is True
    assert "status" in result
    client.sync_once.assert_awaited_once()


async def test_sync_now_failure(sync_env):
    registry, context, _ = sync_env
    client = MagicMock()
    client.sync_once = AsyncMock(side_effect=RuntimeError("network down"))
    bridge = FakeBridge(client=client)
    context.cloud_sync_bridge = bridge
    result = await registry.dispatch("sync.sync_now", {})
    assert result["success"] is False
    assert "network down" in result["error"]


# ── sync.configure ──


async def test_sync_configure_no_settings(sync_env):
    registry, context, _ = sync_env
    context.settings_repository = None
    result = await registry.dispatch("sync.configure", {})
    assert result["success"] is False


async def test_sync_configure_worker_url(sync_env):
    registry, context, settings = sync_env
    result = await registry.dispatch(
        "sync.configure",
        {"worker_url": "https://worker.example.com", "token": "tok123"},
    )
    assert result["success"] is True
    assert result["worker_url"] == "https://worker.example.com"
    assert result["has_token"] is True
    assert settings.get_setting("cloud_sync_worker_url") == "https://worker.example.com"
    assert settings.get_setting("cloud_sync_token") == "tok123"


async def test_sync_configure_strips_trailing_slash(sync_env):
    registry, context, settings = sync_env
    await registry.dispatch(
        "sync.configure", {"worker_url": "https://worker.example.com/"}
    )
    assert settings.get_setting("cloud_sync_worker_url") == "https://worker.example.com"


async def test_sync_configure_interval(sync_env):
    registry, context, settings = sync_env
    result = await registry.dispatch(
        "sync.configure", {"interval_seconds": 600}
    )
    assert result["success"] is True
    assert result["interval_seconds"] == 600
    assert settings.get_setting("cloud_sync_interval_seconds") == 600


# ── sync.enable ──


async def test_sync_enable_missing_config(sync_env):
    registry, context, _ = sync_env
    result = await registry.dispatch("sync.enable", {})
    assert result["success"] is False
    assert "must be configured" in result["error"]


async def test_sync_enable_success(sync_env):
    registry, context, settings = sync_env
    settings.set_setting("cloud_sync_worker_url", "https://worker.example.com")
    settings.set_setting("cloud_sync_token", "tok")
    client = MagicMock()
    client._sync_task = None
    # B-18 附带：handler 改用 is_running()（P1-17 规范，不再直接摸 _sync_task）。
    # 测试显式声明「client 未运行」→ 应调用 bridge.start()。
    client.is_running.return_value = False
    bridge = FakeBridge(client=client)
    context.cloud_sync_bridge = bridge
    result = await registry.dispatch("sync.enable", {})
    assert result["success"] is True
    assert settings.get_setting("cloud_sync_enabled") is True
    bridge.start.assert_awaited_once()


# ── sync.disable ──


async def test_sync_disable(sync_env):
    registry, context, settings = sync_env
    bridge = FakeBridge()
    context.cloud_sync_bridge = bridge
    result = await registry.dispatch("sync.disable", {})
    assert result["success"] is True
    assert result["enabled"] is False
    assert settings.get_setting("cloud_sync_enabled") is False
    bridge.stop.assert_awaited_once()


async def test_sync_disable_no_bridge(sync_env):
    registry, context, settings = sync_env
    result = await registry.dispatch("sync.disable", {})
    assert result["success"] is True
    assert settings.get_setting("cloud_sync_enabled") is False


# ── sync.register_device ──


async def test_register_device_no_bridge(sync_env):
    registry, context, _ = sync_env
    result = await registry.dispatch("sync.register_device", {"name": "Desktop"})
    assert result["success"] is False
    assert "not initialized" in result["error"]


async def test_register_device_no_secret(sync_env):
    registry, context, _ = sync_env
    context.cloud_sync_bridge = FakeBridge()
    result = await registry.dispatch("sync.register_device", {"name": "Desktop"})
    assert result["success"] is False
    assert "register_secret" in result["error"]


async def test_register_device_success(sync_env):
    registry, context, settings = sync_env
    settings.set_setting("cloud_sync_register_secret", "secret123")
    client = MagicMock()
    client.device_id = "device-abc"
    client.register_device = AsyncMock(return_value=True)
    client.close = AsyncMock()
    bridge = FakeBridge(client=client)
    context.cloud_sync_bridge = bridge
    result = await registry.dispatch("sync.register_device", {"name": "MyDesktop"})
    assert result["success"] is True
    assert result["device_id"] == "device-abc"
    client.register_device.assert_awaited_once_with("MyDesktop")
    # client 已存在，不应被关闭
    client.close.assert_not_awaited()


async def test_register_device_temp_client_closed(sync_env):
    """bridge.client 为 None 时，临时构造的 client 应在 finally 中关闭。"""
    registry, context, settings = sync_env
    settings.set_setting("cloud_sync_register_secret", "secret123")
    client = MagicMock()
    client.device_id = "device-temp"
    client.register_device = AsyncMock(return_value=True)
    client.close = AsyncMock()
    bridge = FakeBridge(client=None)
    bridge.build_client = MagicMock(return_value=client)
    context.cloud_sync_bridge = bridge
    result = await registry.dispatch("sync.register_device", {"name": "Temp"})
    assert result["success"] is True
    client.close.assert_awaited_once()


# ── B-18: sync.reset_watermark 生效化（调 client.reset_cursor 而非死 WatermarkStore）──


async def test_reset_watermark_calls_client_reset_cursor(sync_env):
    """B-18: 逃生舱必须调 bridge.client.reset_cursor()（内存+持久双清）。"""
    registry, context, _ = sync_env
    client = MagicMock()
    client.reset_cursor.return_value = 0
    bridge = FakeBridge(client=client)
    context.cloud_sync_bridge = bridge

    result = await registry.dispatch("sync.reset_watermark", {})

    assert result["success"] is True
    assert result["watermark"] == 0
    client.reset_cursor.assert_called_once_with()


async def test_reset_watermark_no_bridge_or_client(sync_env):
    """B-18: bridge 或 client 缺失时返回明确错误（不再静默 success）。"""
    registry, context, _ = sync_env
    # 无 bridge
    result = await registry.dispatch("sync.reset_watermark", {})
    assert result["success"] is False
    assert "not initialized" in result["error"]

    # 有 bridge 无 client
    bridge = FakeBridge(client=None)
    context.cloud_sync_bridge = bridge
    result = await registry.dispatch("sync.reset_watermark", {})
    assert result["success"] is False
    assert "not initialized" in result["error"]


async def test_reset_watermark_propagates_client_exception(sync_env):
    """B-18: client.reset_cursor 异常 → success=False + error（不吞异常假装成功）。"""
    registry, context, _ = sync_env
    client = MagicMock()
    client.reset_cursor.side_effect = RuntimeError("boom")
    bridge = FakeBridge(client=client)
    context.cloud_sync_bridge = bridge

    result = await registry.dispatch("sync.reset_watermark", {})

    assert result["success"] is False
    assert "boom" in result["error"]
