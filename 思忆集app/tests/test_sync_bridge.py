"""SyncBridge 单元测试。

覆盖：
- is_enabled / is_configured（从 settings 读取）
- _ensure_device_id（读取/生成/持久化）
- build_client（配置完整/不完整）
- get_status（无 client / 有 client）
- start / stop 生命周期
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sync.cloud_sync_client import SyncBridge


def _make_context(settings_data=None, *, has_note_repo=True, has_task_repo=True):
    settings_data = settings_data or {}
    settings_repo = SimpleNamespace()
    settings_repo._data = dict(settings_data)
    settings_repo.get_setting = lambda key, default=None: settings_repo._data.get(key, default)
    settings_repo.set_setting = lambda key, value: settings_repo._data.__setitem__(key, value)

    ctx = SimpleNamespace()
    ctx.settings_repository = settings_repo
    ctx.personal_note_repository = MagicMock() if has_note_repo else None
    ctx.personal_task_repository = MagicMock() if has_task_repo else None
    return ctx


# ── is_enabled / is_configured ──

def test_is_enabled_default_false():
    bridge = SyncBridge(_make_context())
    assert bridge.is_enabled() is False


def test_is_enabled_true():
    ctx = _make_context({"cloud_sync_enabled": True})
    assert SyncBridge(ctx).is_enabled() is True


def test_is_configured_default_false():
    bridge = SyncBridge(_make_context())
    assert bridge.is_configured() is False


def test_is_configured_complete():
    ctx = _make_context({
        "cloud_sync_worker_url": "https://sync.example.workers.dev",
        "cloud_sync_token": "my-token",
    })
    assert SyncBridge(ctx).is_configured() is True


def test_is_configured_missing_url():
    ctx = _make_context({"cloud_sync_token": "my-token"})
    assert SyncBridge(ctx).is_configured() is False


def test_is_configured_missing_token():
    ctx = _make_context({"cloud_sync_worker_url": "https://sync.example.workers.dev"})
    assert SyncBridge(ctx).is_configured() is False


# ── _ensure_device_id ──

def test_ensure_device_id_returns_existing():
    ctx = _make_context({"cloud_sync_device_id": "desktop-existing"})
    bridge = SyncBridge(ctx)
    assert bridge._ensure_device_id() == "desktop-existing"


def test_ensure_device_id_generates_and_persists():
    ctx = _make_context()  # 无 device_id
    bridge = SyncBridge(ctx)
    device_id = bridge._ensure_device_id()
    assert device_id.startswith("desktop-")
    # 应已持久化到 settings
    assert ctx.settings_repository._data["cloud_sync_device_id"] == device_id


def test_ensure_device_id_stable_across_calls():
    ctx = _make_context()
    bridge = SyncBridge(ctx)
    id1 = bridge._ensure_device_id()
    id2 = bridge._ensure_device_id()
    assert id1 == id2  # 第二次读取已持久化的值


def test_ensure_device_id_persist_failure_does_not_raise():
    """settings 写入失败时应返回生成的 id，不抛异常。"""
    ctx = _make_context()
    ctx.settings_repository.set_setting = MagicMock(side_effect=RuntimeError("disk full"))
    bridge = SyncBridge(ctx)
    device_id = bridge._ensure_device_id()
    assert device_id.startswith("desktop-")


# ── build_client ──

def test_build_client_complete_config():
    ctx = _make_context({
        "cloud_sync_worker_url": "https://sync.example.workers.dev",
        "cloud_sync_token": "my-token",
        "cloud_sync_register_secret": "reg-secret",
        "cloud_sync_device_id": "desktop-abc",
    })
    bridge = SyncBridge(ctx)
    client = bridge.build_client()
    assert client is not None
    assert client.worker_url == "https://sync.example.workers.dev"
    assert client.sync_token == "my-token"
    assert client.register_secret == "reg-secret"
    assert client.device_id == "desktop-abc"
    assert client.note_repo is not None
    assert client.task_repo is not None
    assert client.settings_repo is not None


def test_build_client_missing_url_returns_none():
    ctx = _make_context({"cloud_sync_token": "my-token"})
    bridge = SyncBridge(ctx)
    assert bridge.build_client() is None


def test_build_client_missing_token_returns_none():
    ctx = _make_context({"cloud_sync_worker_url": "https://sync.example.workers.dev"})
    bridge = SyncBridge(ctx)
    assert bridge.build_client() is None


def test_build_client_strips_trailing_slash():
    ctx = _make_context({
        "cloud_sync_worker_url": "https://sync.example.workers.dev/",
        "cloud_sync_token": "tok",
        "cloud_sync_device_id": "dev1",
    })
    bridge = SyncBridge(ctx)
    client = bridge.build_client()
    assert client.worker_url == "https://sync.example.workers.dev"


def test_build_client_no_repos_passes_none():
    ctx = _make_context(
        {"cloud_sync_worker_url": "https://sync.example.workers.dev", "cloud_sync_token": "tok"},
        has_note_repo=False, has_task_repo=False,
    )
    bridge = SyncBridge(ctx)
    client = bridge.build_client()
    assert client.note_repo is None
    assert client.task_repo is None


# ── get_status ──

def test_get_status_no_client():
    ctx = _make_context({"cloud_sync_enabled": False})
    bridge = SyncBridge(ctx)
    status = bridge.get_status()
    assert status["enabled"] is False
    assert status["configured"] is False
    assert status["running"] is False
    assert "message" in status


def test_get_status_no_client_but_configured():
    ctx = _make_context({
        "cloud_sync_enabled": True,
        "cloud_sync_worker_url": "https://sync.example.workers.dev",
        "cloud_sync_token": "tok",
    })
    bridge = SyncBridge(ctx)
    status = bridge.get_status()
    assert status["enabled"] is True
    assert status["configured"] is True
    assert status["running"] is False


def test_get_status_with_client():
    ctx = _make_context({
        "cloud_sync_enabled": True,
        "cloud_sync_worker_url": "https://sync.example.workers.dev",
        "cloud_sync_token": "tok",
        "cloud_sync_device_id": "dev1",
    })
    bridge = SyncBridge(ctx)
    bridge.client = bridge.build_client()
    status = bridge.get_status()
    assert status["enabled"] is True
    assert status["configured"] is True
    assert status["device_id"] == "dev1"
    assert "running" in status


# ── start ──

@pytest.mark.asyncio
async def test_start_disabled_returns_false():
    ctx = _make_context()  # enabled=False
    bridge = SyncBridge(ctx)
    ok = await bridge.start()
    assert ok is False
    assert bridge.client is None


@pytest.mark.asyncio
async def test_start_enabled_but_not_configured_returns_false():
    ctx = _make_context({"cloud_sync_enabled": True})  # 无 url/token
    bridge = SyncBridge(ctx)
    ok = await bridge.start()
    assert ok is False
    assert bridge.client is None


@pytest.mark.asyncio
async def test_start_enabled_and_configured():
    ctx = _make_context({
        "cloud_sync_enabled": True,
        "cloud_sync_worker_url": "https://sync.example.workers.dev",
        "cloud_sync_token": "tok",
        "cloud_sync_device_id": "dev1",
        "cloud_sync_interval_seconds": 600,
    })
    bridge = SyncBridge(ctx)
    with patch.object(bridge, "build_client") as mock_build:
        mock_client = MagicMock()
        mock_client._sync_task = None
        mock_client.start_sync_loop = MagicMock()
        mock_build.return_value = mock_client
        ok = await bridge.start()
    assert ok is True
    assert bridge.client is mock_client
    mock_client.start_sync_loop.assert_called_once_with(interval_seconds=600)


@pytest.mark.asyncio
async def test_start_already_running_returns_true():
    ctx = _make_context({
        "cloud_sync_enabled": True,
        "cloud_sync_worker_url": "https://sync.example.workers.dev",
        "cloud_sync_token": "tok",
    })
    bridge = SyncBridge(ctx)
    # 模拟已运行的 client
    mock_client = MagicMock()
    mock_client._sync_task = MagicMock()
    mock_client._sync_task.done.return_value = False
    bridge.client = mock_client
    ok = await bridge.start()
    assert ok is True
    # 不应重新构造 client
    assert bridge.client is mock_client


# ── stop ──

@pytest.mark.asyncio
async def test_stop_with_client():
    ctx = _make_context()
    bridge = SyncBridge(ctx)
    mock_client = MagicMock()
    mock_client.stop_sync_loop = MagicMock()
    mock_client.close = AsyncMock()
    bridge.client = mock_client
    await bridge.stop()
    mock_client.stop_sync_loop.assert_called_once()
    mock_client.close.assert_called_once()
    assert bridge.client is None


@pytest.mark.asyncio
async def test_stop_without_client():
    ctx = _make_context()
    bridge = SyncBridge(ctx)
    await bridge.stop()  # 不应抛异常
    assert bridge.client is None


# ── _get_setting 容错 ──

def test_get_setting_no_repo_returns_default():
    ctx = SimpleNamespace()
    ctx.settings_repository = None
    bridge = SyncBridge(ctx)
    assert bridge._get_setting("any_key", "default") == "default"


def test_get_setting_repo_error_returns_default():
    ctx = SimpleNamespace()
    repo = MagicMock()
    repo.get_setting = MagicMock(side_effect=RuntimeError("db error"))
    ctx.settings_repository = repo
    bridge = SyncBridge(ctx)
    assert bridge._get_setting("any_key", "default") == "default"
