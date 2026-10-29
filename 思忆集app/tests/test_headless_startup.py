"""Tests for 思忆集app/backend/headless_main.py startup utilities."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# Import after adjusting sys.path so the module can locate app_runtime etc.
import backend.headless_main as headless_main  # noqa: E402


class TestWsConfigDefaults:
    """WS_HOST/WS_PORT must read environment variables with safe defaults."""

    def test_default_ws_host(self, monkeypatch):
        """N-D 收口（2026-09-17）：SIYI_WS_HOST 未设时默认 127.0.0.1（仅本机），
        Tailscale/LAN 须显式设 SIYI_WS_HOST=0.0.0.0 开启。"""
        monkeypatch.delenv("SIYI_WS_HOST", raising=False)
        monkeypatch.delenv("SIYI_WS_PORT", raising=False)
        # Re-import to re-evaluate module-level constants.
        import importlib

        mod = importlib.reload(headless_main)
        assert mod.WS_HOST == "127.0.0.1"

    def test_empty_ws_host_falls_back_to_loopback(self, monkeypatch):
        """SIYI_WS_HOST 设为空/纯空白时不得退化为异常绑定，须兜底 127.0.0.1。"""
        monkeypatch.setenv("SIYI_WS_HOST", "   ")
        monkeypatch.delenv("SIYI_WS_PORT", raising=False)
        import importlib

        mod = importlib.reload(headless_main)
        assert mod.WS_HOST == "127.0.0.1"

    def test_default_ws_port(self, monkeypatch):
        """When SIYI_WS_PORT is unset, WS_PORT defaults to 18765."""
        monkeypatch.delenv("SIYI_WS_HOST", raising=False)
        monkeypatch.delenv("SIYI_WS_PORT", raising=False)
        import importlib

        mod = importlib.reload(headless_main)
        assert mod.WS_PORT == 18765

    def test_ws_host_from_env(self, monkeypatch):
        monkeypatch.setenv("SIYI_WS_HOST", "0.0.0.0")
        monkeypatch.delenv("SIYI_WS_PORT", raising=False)
        import importlib

        mod = importlib.reload(headless_main)
        assert mod.WS_HOST == "0.0.0.0"

    def test_ws_port_from_env(self, monkeypatch):
        monkeypatch.setenv("SIYI_WS_PORT", "20000")
        monkeypatch.delenv("SIYI_WS_HOST", raising=False)
        import importlib

        mod = importlib.reload(headless_main)
        assert mod.WS_PORT == 20000


class TestSecurityFailClosedGate:
    """N-D auth fail-closed 守卫：非回环绑定 + 认证不可用 = 拒启。"""

    def test_loopback_without_auth_allowed(self):
        """回环绑定允许无认证启动（个人单机自用形态）。"""
        headless_main.security_fail_closed_gate("127.0.0.1", auth_enabled=False)

    def test_localhost_alias_without_auth_allowed(self):
        for host in ("localhost", "::1", "127.0.0.53", "::ffff:127.0.0.1"):
            headless_main.security_fail_closed_gate(host, auth_enabled=False)

    def test_non_loopback_without_auth_refuses(self):
        """0.0.0.0 / 指定网卡地址 + 认证不可用 → SystemExit(8)。"""
        for host in ("0.0.0.0", "192.168.1.10", "::"):
            with pytest.raises(SystemExit) as exc_info:
                headless_main.security_fail_closed_gate(host, auth_enabled=False)
            assert exc_info.value.code == 8

    def test_non_loopback_with_auth_allowed(self):
        """Tailscale/LAN 显式开启 + 认证可用 = 合法组合，正常放行。"""
        headless_main.security_fail_closed_gate("0.0.0.0", auth_enabled=True)

    def test_gate_wired_into_async_main(self):
        """接线锁：async_main 必须在构造 WebSocketServer 前调用守卫，
        否则守卫只是死代码（重依赖全 mock 跑整条启动流不现实，用源码断言）。"""
        import inspect

        src = inspect.getsource(headless_main.async_main)
        call_at = src.index("security_fail_closed_gate(")
        ctor_at = src.index("WebSocketServer(")
        assert call_at < ctor_at
        # 且传入的是有效认证标志，而非写死的 True
        assert "_auth_effective" in src


class TestAcquireLock:
    """Single-instance file locking behavior."""

    def test_acquire_lock_returns_fd_on_windows(self, tmp_path, monkeypatch):
        """On Windows, acquire_lock returns a non-negative fd."""
        if not headless_main.HAS_MSVCRT:
            pytest.skip("msvcrt not available")
        monkeypatch.setenv("TEMP", str(tmp_path))

        fd = headless_main.acquire_lock()
        assert fd is not None
        assert isinstance(fd, int)
        assert fd >= 0
        headless_main.release_lock(fd)

    def test_acquire_lock_returns_none_when_already_locked(self, tmp_path, monkeypatch):
        """A second acquire on the same path must fail while the first fd is open."""
        if not headless_main.HAS_MSVCRT:
            pytest.skip("msvcrt not available")
        monkeypatch.setenv("TEMP", str(tmp_path))
        # P0-1：关闭僵死接管，避免测试进程自身（锁持有者）被探测/终止
        monkeypatch.setenv("SIYI_LOCK_TAKEOVER", "0")

        first_fd = headless_main.acquire_lock()
        assert first_fd is not None
        try:
            second_fd = headless_main.acquire_lock()
            assert second_fd is None
        finally:
            headless_main.release_lock(first_fd)

    def test_acquire_lock_returns_none_without_msvcrt(self, monkeypatch):
        """On platforms without msvcrt, acquire_lock short-circuits to None."""
        # P0-1：锁实现下沉至 backend.instance_lock，补丁须打在实现模块上
        from backend import instance_lock

        monkeypatch.setattr(instance_lock, "HAS_MSVCRT", False)
        assert headless_main.acquire_lock() is None


class TestReleaseLock:
    """release_lock must be safe and idempotent."""

    def test_release_lock_with_none(self):
        headless_main.release_lock(None)  # should not raise

    def test_release_lock_releases_real_fd(self, tmp_path, monkeypatch):
        if not headless_main.HAS_MSVCRT:
            pytest.skip("msvcrt not available")
        monkeypatch.setenv("TEMP", str(tmp_path))

        fd = headless_main.acquire_lock()
        assert fd is not None
        headless_main.release_lock(fd)
        # After release, a new acquire should succeed again.
        fd2 = headless_main.acquire_lock()
        assert fd2 is not None
        headless_main.release_lock(fd2)


class TestAsyncMain:
    """async_main startup/shutdown flow with heavy dependencies mocked."""

    @pytest.fixture
    def stop_immediately(self, monkeypatch):
        """Make asyncio.Event.wait() return immediately so shutdown runs."""
        real_event_class = asyncio.Event

        class FastEvent(real_event_class):
            async def wait(self):
                return None

        monkeypatch.setattr(asyncio, "Event", FastEvent)

    @pytest.fixture
    def patched_deps(self, monkeypatch, tmp_path):
        """Provide mocks for AppContext, server, registry, bridge, schedulers."""
        mocks: dict = {}

        # AppContext
        mock_context = MagicMock()
        mock_context.ai_client = MagicMock()
        mock_settings = MagicMock()
        mock_settings.get_setting.side_effect = lambda key, default=None: default
        mock_context.settings_repository = mock_settings
        mocks["AppContext"] = mock_context

        # WebSocketServer
        mock_server_class = MagicMock()
        mock_server = MagicMock()
        mock_server.start = AsyncMock()
        mock_server.stop = AsyncMock()
        mock_server_class.return_value = mock_server
        mocks["WebSocketServer"] = mock_server
        mocks["WebSocketServer_class"] = mock_server_class

        # MethodRegistry
        mock_registry_class = MagicMock()
        mock_registry = MagicMock()
        mock_registry_class.return_value = mock_registry
        mocks["MethodRegistry"] = mock_registry

        # EventBridge
        mock_bridge_class = MagicMock()
        mock_bridge = MagicMock()
        mock_bridge_class.return_value = mock_bridge
        mocks["EventBridge"] = mock_bridge

        # register_all (async function — must use AsyncMock, not MagicMock)
        mock_register_all = AsyncMock()
        mocks["register_all"] = mock_register_all

        # info_scheduler
        mock_info_scheduler = MagicMock()
        mocks["info_scheduler"] = mock_info_scheduler

        # vocab_mood_coordinator
        mock_vocab_mood = MagicMock()
        mocks["vocab_mood_coordinator"] = mock_vocab_mood

        # due_reminder_scheduler
        mock_due_reminder = MagicMock()
        mocks["due_reminder_scheduler"] = mock_due_reminder

        # InfoCollectorService
        mock_collector_service_class = MagicMock()
        mock_collector = MagicMock()
        mock_collector_service_class.return_value = mock_collector
        mocks["InfoCollectorService"] = mock_collector
        mocks["InfoCollectorService_class"] = mock_collector_service_class

        # Ensure submodule attributes exist on their parent packages so
        # monkeypatch can replace them.
        import backend.info_scheduler  # noqa: F401
        import backend.vocab_mood  # noqa: F401
        import backend.due_reminder  # noqa: F401
        import services.info_collector_service  # noqa: F401

        # AppContext is imported at module load time in headless_main, so we
        # must patch the name bound in that module.
        monkeypatch.setattr(headless_main, "AppContext", lambda **kwargs: mock_context)
        # N-D：上方 reload 类测试可能把模块常量 WS_HOST 污染为 0.0.0.0；
        # 本组测试走无认证 mock（auth_token=None），须钉回回环避免误触
        # security_fail_closed_gate 拒启（守卫语义由 TestSecurityFailClosedGate 专测）。
        monkeypatch.setattr(headless_main, "WS_HOST", "127.0.0.1")
        # Some CI/test consoles use GBK encoding and cannot print the special
        # characters used in the "backend ready" banner.
        monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)
        monkeypatch.setattr("backend.ws_server.WebSocketServer", mock_server_class)
        monkeypatch.setattr("backend.event_bridge.EventBridge", mock_bridge_class)
        monkeypatch.setattr("backend.method_registry.MethodRegistry", mock_registry_class)
        monkeypatch.setattr("backend.handlers.register_all", mock_register_all)
        monkeypatch.setattr("backend.info_scheduler", mock_info_scheduler)
        monkeypatch.setattr("backend.vocab_mood.vocab_mood_coordinator", mock_vocab_mood)
        monkeypatch.setattr("backend.due_reminder.due_reminder_scheduler", mock_due_reminder)
        monkeypatch.setattr(
            "services.info_collector_service.InfoCollectorService",
            mock_collector_service_class,
        )

        return mocks

    def test_async_main_runs_startup_and_shutdown(self, patched_deps, stop_immediately):
        """async_main initializes context, starts server, then shuts down cleanly."""

        async def _run():
            await headless_main.async_main()

        asyncio.run(_run())

        mocks = patched_deps
        mocks["AppContext"].stop_background_services.assert_called_once()
        mocks["WebSocketServer"].start.assert_awaited_once()
        mocks["WebSocketServer"].stop.assert_awaited_once()
        mocks["EventBridge"].start_bridging.assert_called_once()
        mocks["EventBridge"].stop_bridging.assert_called_once()
        mocks["register_all"].assert_called_once()
        mocks["info_scheduler"].configure.assert_called_once()
        mocks["info_scheduler"].cancel.assert_called_once()
        mocks["vocab_mood_coordinator"].start.assert_called_once()
        mocks["vocab_mood_coordinator"].stop.assert_called_once()
        mocks["due_reminder_scheduler"].start.assert_called_once()
        mocks["due_reminder_scheduler"].stop.assert_called_once()

    def test_async_main_signature(self):
        """Sanity check that async_main is importable and is a coroutine function."""
        import inspect

        assert inspect.iscoroutinefunction(headless_main.async_main)
        assert headless_main.async_main.__name__ == "async_main"


class TestMainEntryPoint:
    """Synchronous main() entry point."""

    def test_main_is_callable(self):
        assert callable(headless_main.main)

    def test_main_exits_when_lock_held(self, tmp_path, monkeypatch):
        """main() should exit with code 1 when another instance holds the lock."""
        if not headless_main.HAS_MSVCRT:
            pytest.skip("msvcrt not available")
        monkeypatch.setenv("TEMP", str(tmp_path))
        # P0-1：关闭僵死接管——本测试中锁持有者就是 pytest 进程自身，
        # 接管分支会把 pytest 判为 zombie backend 并 taskkill 自杀（EXIT=1 无 summary）。
        # 关闭后 main() 走正常拒绝路径 sys.exit(1)，正是本测试要验证的语义。
        monkeypatch.setenv("SIYI_LOCK_TAKEOVER", "0")

        first_fd = headless_main.acquire_lock()
        assert first_fd is not None
        try:
            with pytest.raises(SystemExit) as exc_info:
                headless_main.main()
            assert exc_info.value.code == 1
        finally:
            headless_main.release_lock(first_fd)
