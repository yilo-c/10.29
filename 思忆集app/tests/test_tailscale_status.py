"""system.tailscale_status JSON-RPC handler 单元测试。

验证 backend/handlers/_system_settings.py 的 system.tailscale_status：
1. 注册到 registry
2. Tailscale CLI 可用且返回有效 JSON → 解析 Self.TailscaleIPs
3. Tailscale CLI 不可用 → 回退到网卡扫描，识别 100.64.0.0/10 段
4. 无 Tailscale IP → 返回 connected=False

不依赖真实 Tailscale 安装，全部通过 mock 实现。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.method_registry import MethodRegistry
from backend.handlers._system_settings import register_system_settings


def _make_context():
    """构造最小可用的 AppContext mock。"""
    ctx = SimpleNamespace()
    ctx.settings_repository = SimpleNamespace(
        get_setting=lambda key, default=None: default,
        set_setting=lambda key, value: None,
    )
    ctx.ai_client = None
    ctx.memory_index_service = None
    ctx.knowledge_graph_store = None
    ctx.recreate_ai_client = MagicMock()
    ctx.autonomous_assistant_manager = None
    return ctx


def _make_registry():
    registry = MethodRegistry()
    ctx = _make_context()
    ak_service = MagicMock()
    register_system_settings(registry, ctx, ws_port=18765, ak_service=ak_service)
    return registry


# ── 注册完整性 ──

def test_tailscale_status_registered():
    """system.tailscale_status 应被注册。"""
    registry = _make_registry()
    assert registry.has_method("system.tailscale_status"), \
        "system.tailscale_status not registered"


# ── CLI 路径 ──

@pytest.mark.asyncio
async def test_tailscale_status_via_cli_success():
    """tailscale CLI 返回有效 JSON → 解析 Self.TailscaleIPs。"""
    registry = _make_registry()

    cli_output = json.dumps({
        "Self": {
            "TailscaleIPs": ["100.64.1.2"],
            "HostName": "my-desktop",
        }
    }).encode("utf-8")

    fake_proc = AsyncMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(cli_output, b""))

    with patch("shutil.which", return_value="/usr/bin/tailscale"), \
         patch("asyncio.create_subprocess_exec", return_value=fake_proc):
        result = await registry.dispatch("system.tailscale_status", {})

    assert result["connected"] is True
    assert result["tailscale_ip"] == "100.64.1.2"
    assert result["hostname"] == "my-desktop"


@pytest.mark.asyncio
async def test_tailscale_status_via_cli_failure_falls_back_to_scan():
    """tailscale CLI 返回非零退出码 → 回退到网卡扫描。"""
    registry = _make_registry()

    fake_proc = AsyncMock()
    fake_proc.returncode = 1
    fake_proc.communicate = AsyncMock(return_value=(b"", b"error"))

    # 网卡扫描也找不到 100.x.x.x
    with patch("shutil.which", return_value="/usr/bin/tailscale"), \
         patch("asyncio.create_subprocess_exec", return_value=fake_proc), \
         patch("socket.gethostname", return_value="myhost"), \
         patch("socket.getaddrinfo", return_value=[]):
        result = await registry.dispatch("system.tailscale_status", {})

    assert result["connected"] is False
    assert result["tailscale_ip"] is None


# ── 网卡扫描回退路径 ──

@pytest.mark.asyncio
async def test_tailscale_status_scan_finds_100_x_ip():
    """无 CLI → 网卡扫描发现 100.64.x.x → connected=True。"""
    registry = _make_registry()

    # 模拟 getaddrinfo 返回含 Tailscale IP 的地址列表
    fake_addrs = [
        # 普通局域网 IP — 应被忽略
        ("", 0, 0, "", ("192.168.1.5", 0)),
        # Tailscale IP — 应被识别
        ("", 0, 0, "", ("100.64.10.20", 0)),
    ]

    with patch("shutil.which", return_value=None), \
         patch("socket.gethostname", return_value="myhost"), \
         patch("socket.getaddrinfo", return_value=fake_addrs):
        result = await registry.dispatch("system.tailscale_status", {})

    assert result["connected"] is True
    assert result["tailscale_ip"] == "100.64.10.20"
    assert result["hostname"] == "myhost"


@pytest.mark.asyncio
async def test_tailscale_status_scan_ignores_non_tailscale_ips():
    """网卡扫描时，非 100.64.0.0/10 段的 IP 应被忽略。"""
    registry = _make_registry()

    fake_addrs = [
        ("", 0, 0, "", ("192.168.1.5", 0)),
        ("", 0, 0, "", ("10.0.0.1", 0)),
        ("", 0, 0, "", ("172.16.0.1", 0)),
        # 100.0.x.x 不在 Tailscale 段（应为 100.64-127）
        ("", 0, 0, "", ("100.0.0.1", 0)),
        # 100.200.x.x 超出上界
        ("", 0, 0, "", ("100.200.0.1", 0)),
    ]

    with patch("shutil.which", return_value=None), \
         patch("socket.gethostname", return_value="myhost"), \
         patch("socket.getaddrinfo", return_value=fake_addrs):
        result = await registry.dispatch("system.tailscale_status", {})

    assert result["connected"] is False
    assert result["tailscale_ip"] is None


@pytest.mark.asyncio
async def test_tailscale_status_no_tailscale_returns_disconnected():
    """无 CLI + 无 Tailscale IP → connected=False。"""
    registry = _make_registry()

    with patch("shutil.which", return_value=None), \
         patch("socket.gethostname", return_value="myhost"), \
         patch("socket.getaddrinfo", return_value=[
             ("", 0, 0, "", ("192.168.1.5", 0)),
         ]):
        result = await registry.dispatch("system.tailscale_status", {})

    assert result["connected"] is False
    assert result["tailscale_ip"] is None
    assert result["hostname"] is None


# ── 边界条件 ──

@pytest.mark.asyncio
async def test_tailscale_status_cli_timeout_falls_back_to_scan():
    """tailscale CLI 超时 → 回退到网卡扫描。"""
    registry = _make_registry()

    fake_proc = AsyncMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

    with patch("shutil.which", return_value="/usr/bin/tailscale"), \
         patch("asyncio.create_subprocess_exec", return_value=fake_proc), \
         patch("socket.gethostname", return_value="myhost"), \
         patch("socket.getaddrinfo", return_value=[
             ("", 0, 0, "", ("100.77.88.99", 0)),
         ]):
        result = await registry.dispatch("system.tailscale_status", {})

    # CLI 超时后回退到扫描，应找到 Tailscale IP
    assert result["connected"] is True
    assert result["tailscale_ip"] == "100.77.88.99"
