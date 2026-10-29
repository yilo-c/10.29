"""auth.rotate 响应先行 / 延迟踢连接回归测试（P2 修复，rpc-census-2026-08-30）。

缺陷：_rpc_auth_rotate 在 rotate 后同步 close_all(1008)，把调用方自己的连接
先于 RPC 响应关掉——客户端只见 "ws closed: 8"，成功响应（含新 token）永远
送不到（全量普查中该 "空参调用" 实为本方法的合法调用——它没有参数）。
修复：handler 先返回响应，延迟 1s 再异步踢出全部旧连接。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

import backend.headless_main as headless_main


def _fake_server(token: str = "tok-new") -> MagicMock:
    server = MagicMock()
    server.rotate_auth_token = MagicMock(return_value=token)
    server.connection_manager = MagicMock()
    server.connection_manager.close_all = AsyncMock(return_value=1)
    return server


@pytest.mark.asyncio
async def test_rotate_returns_response_before_kick():
    """handler 返回（响应可写回）时连接尚未被关闭。"""
    server = _fake_server()
    result = await headless_main.auth_rotate_impl(server)
    assert result == {"success": True, "rotated": True, "token": "tok-new"}
    server.connection_manager.close_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_kick_fires_after_delay():
    """延迟窗口后按 1008/token rotated 踢出全部连接（设计意图保留）。"""
    server = _fake_server()
    await headless_main.auth_rotate_impl(server)
    await asyncio.sleep(headless_main._KICK_DELAY_SECONDS + 0.25)
    server.connection_manager.close_all.assert_awaited_once_with(code=1008, message=b"token rotated", restart_heartbeat=True)


@pytest.mark.asyncio
async def test_rotate_failure_returns_clean_dict():
    """rotate 失败 → success=False 结构化返回，且不调度踢连接。"""
    server = _fake_server()
    server.rotate_auth_token = MagicMock(side_effect=RuntimeError("boom"))
    result = await headless_main.auth_rotate_impl(server)
    assert result["success"] is False
    assert "boom" in result["error"]
    server.connection_manager.close_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_without_running_loop_is_noop(monkeypatch):
    """无事件循环时调度为 no-op（不抛 RuntimeError）。"""

    def _raise():
        raise RuntimeError("no loop")

    monkeypatch.setattr(headless_main.asyncio, "get_running_loop", _raise)
    assert headless_main._schedule_deferred_close_all(MagicMock(), code=1008, message=b"x") is None
