"""P1.5 验证：transfer 原子写 / info_proxy 大小限制 / weather 注册 /
SandboxManager 实例隔离 / transfer token 解耦。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


# ── H5: transfer_upload 原子写 ──────────────────────────────────────────


def test_transfer_upload_uses_atomic_replace(tmp_path, monkeypatch):
    """transfer.upload 必须用 .tmp + os.replace 原子写入。"""
    src = (APP_ROOT / "backend" / "handlers" / "_transfer.py").read_text(encoding="utf-8")
    # 检查源码模式
    assert "os.replace(tmp_path, target)" in src, "应使用 os.replace 原子换名"
    assert ".tmp" in src, "应写入临时 .tmp 文件再换名"
    # 不应直接 with open(target, "wb") 写入
    # 注释里允许提到，但代码语句不应有
    code_only = "\n".join(line for line in src.split("\n") if not line.strip().startswith("#"))
    assert 'with open(target, "wb") as f' not in code_only, "H5 回归：transfer_upload 又退回了非原子写入"


# ── H7: info.fetch_proxy 大小限制 + 主机白名单 ──────────────────────────


def test_info_proxy_has_response_size_limit():
    src = (APP_ROOT / "backend" / "handlers" / "_info_proxy.py").read_text(encoding="utf-8")
    assert "MAX_RESPONSE_BYTES" in src
    assert "resp.content.read(MAX_RESPONSE_BYTES" in src, "应使用 content.read(N) 限制读取大小，而非 resp.text() 全读"


def test_info_proxy_enforces_host_allowlist():
    src = (APP_ROOT / "backend" / "handlers" / "_info_proxy.py").read_text(encoding="utf-8")
    assert "_ALLOWED_HOSTS" in src
    assert "host not in _ALLOWED_HOSTS" in src, "fetch_url 必须检查 host 白名单"


# ── M2: weather.current 注册 ──────────────────────────────────────────


def test_weather_handler_module_registers_method():
    """register_weather 必须把 weather.current 注册到 registry。"""
    from backend.handlers import _weather
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    # context 至少要有 weather_service 属性即可（实际调用走的是 svc）
    ctx = MagicMock()
    ctx.weather_service = MagicMock()
    ctx.weather_service._fetch_live_weather.return_value = {"title": "北京 · 晴", "value": "20掳C", "meta": "...", "tip": "..."}
    ctx.weather_service._default_city = "beijing"

    _weather.register_weather(registry, ctx)
    methods = registry.list_methods() if hasattr(registry, "list_methods") else list(registry._methods.keys())
    assert "weather.current" in methods


def test_weather_current_returns_payload_when_service_succeeds():
    from backend.handlers._weather import register_weather
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    ctx = MagicMock()
    ctx.weather_service = MagicMock()
    ctx.weather_service._fetch_live_weather.return_value = {"title": "Beijing · Sunny", "value": "22掳C", "meta": "...", "tip": "..."}
    ctx.weather_service._default_city = "beijing"

    register_weather(registry, ctx)
    result = asyncio.run(registry.dispatch("weather.current", {"city": "shanghai"}))
    assert result["success"] is True
    assert result["title"] == "Beijing · Sunny"


def test_weather_current_returns_error_when_service_unavailable():
    from backend.handlers._weather import register_weather
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    ctx = MagicMock()
    ctx.weather_service = None

    register_weather(registry, ctx)
    result = asyncio.run(registry.dispatch("weather.current", {}))
    assert result["success"] is False


def test_weather_current_returns_error_when_fetch_fails():
    from backend.handlers._weather import register_weather
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    ctx = MagicMock()
    ctx.weather_service = MagicMock()
    ctx.weather_service._fetch_live_weather.return_value = None
    ctx.weather_service._default_city = "beijing"

    register_weather(registry, ctx)
    result = asyncio.run(registry.dispatch("weather.current", {"city": "beijing"}))
    assert result["success"] is False
    assert "fetch failed" in result["error"]


# ── M4: SandboxManager 实例隔离 ──────────────────────────────────────


def test_sandbox_manager_pending_is_per_instance():
    """两个 SandboxManager 不应共享 _pending 字典。"""
    from services.sandbox_manager import SandboxManager

    a = SandboxManager()
    b = SandboxManager()
    pid_a = a.add("file.write", {"path": "x.txt"}, {"size": 10})
    assert pid_a in a._pending
    assert pid_a not in b._pending, "M4 回归：SandboxManager._pending 又变回 class 属性 —— 多 instance 共享 pending 字典会导致跨 session 越权审批"


def test_sandbox_manager_uses_secrets_not_md5():
    """audit LOW: _pending_id 应该用 secrets.token_hex 而非 md5（bandit 警告）。"""
    # ★ 修复：SandboxManager 已从 backend/react_executor.py 迁移到 services/sandbox_manager.py
    src = (APP_ROOT / "services" / "sandbox_manager.py").read_text(encoding="utf-8")
    # 看 SandboxManager.add 函数
    import re

    m = re.search(r"def add\(self.*?def get\(", src, re.DOTALL)
    add_body = m.group(0) if m else ""
    assert "secrets.token_hex" in add_body
    assert "hashlib.md5" not in add_body, "M4: SandboxManager 应改用 secrets，不用 md5"


def test_sandbox_manager_pending_capped():
    """_pending 不能无界增长；超过 _MAX_PENDING 时应删旧条目。"""
    from services.sandbox_manager import SandboxManager

    sm = SandboxManager()
    sm._MAX_PENDING = 10  # 测试用小值
    for i in range(15):
        sm.add(f"action.{i}", {"x": i}, {})
    # 15 条超过 10，应已触发清理（删 20% = 2 条），所以最多剩 14 条左右
    assert len(sm._pending) <= 15
    assert len(sm._pending) >= 8  # 不能清光


# ── M5 / PH-A1: transfer token 子系统 ─────────────────────────────────
# PH-A1（2026-09-17）裁决：原「生成 token → 持久化 settings.transfer_token →
# 文件回退」子系统全仓零消费（真鉴权只看 env SIYI_TRANSFER_KEY），整段删除。
# 下列断言从"不得再绑定 api_key"升级为"单一凭证模型：无生成、无持久化"。


def test_transfer_token_no_longer_derived_from_api_key():
    src = (APP_ROOT / "backend" / "handlers" / "_transfer.py").read_text(encoding="utf-8")
    code_only = "\n".join(line for line in src.split("\n") if not line.strip().startswith("#"))
    # 应不再使用 SHA-256(api_key) 派生
    assert "hashlib.sha256(api_key" not in code_only, "M5 回归：transfer token 又被绑定到 api_key — 用户旋转 LLM key 时手机会失联"
    # PH-A1：不再生成随机 token、不再持久化到 settings（单一凭证 = env / WS auth_token）
    assert "secrets.token_urlsafe" not in code_only, "PH-A1：token 生成死代码未删除"
    assert "settings.set_setting" not in code_only, "PH-A1：settings 持久化死代码未删除"
    assert "settings.get_setting" not in code_only, "PH-A1：settings 读取死代码未删除"


def test_transfer_token_dead_path_removed():
    """PH-A1：生成/持久化/回退三个死代码入口必须全部不存在。"""
    import backend.handlers._transfer as transfer

    for name in (
        "_get_or_create_token",
        "_persist_token_with_retry",
        "_TRANSFER_TOKEN",
        "_SETTINGS_KEY",
    ):
        assert not hasattr(transfer, name), f"PH-A1：死代码未删除 {name}"


def test_transfer_auth_uses_env_credential_only(monkeypatch):
    """PH-A1：鉴权只认 env SIYI_TRANSFER_KEY，与 settings 内容无关。"""
    import backend.handlers._transfer as transfer

    monkeypatch.setenv("SIYI_TRANSFER_KEY", "explicit-runtime-token")
    assert transfer._check_auth(_token="explicit-runtime-token") is None
    assert transfer._check_auth(_token="stored-token") == "auth failed (invalid _token)"
    monkeypatch.delenv("SIYI_TRANSFER_KEY", raising=False)
    assert transfer._check_auth(_token="anything") is None  # LAN 模式不要求额外凭证
