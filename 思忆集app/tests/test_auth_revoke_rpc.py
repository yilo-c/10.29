"""auth.revoke 响应先行 / 延迟踢连接回归测试（P1-1 修复，wave-4）。

缺陷：_rpc_auth_revoke 在吊销后内联 await close_all(1008)，把调用方自己的
连接先于 RPC 响应关掉——客户端只见 "ws closed: 8"，成功响应永远送不到。
与 auth.rotate 修复前（rpc-census-2026-08-30）同构。
修复：抽模块级 auth_revoke_impl，复用 _schedule_deferred_close_all——
先返回响应，延迟 1s 再异步踢出全部旧连接。
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


def _fake_server() -> MagicMock:
    server = MagicMock()
    server.rotate_auth_token = MagicMock(return_value="tok-new")
    server.revoke_all = MagicMock(return_value=None)
    server.connection_manager = MagicMock()
    server.connection_manager.close_all = AsyncMock(return_value=1)
    return server


@pytest.mark.asyncio
async def test_revoke_returns_response_before_kick():
    """handler 返回（响应可写回）时连接尚未被关闭。"""
    server = _fake_server()
    result = await headless_main.auth_revoke_impl(server)
    assert result == {"success": True, "revoked": True, "rotated": True}
    server.connection_manager.close_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoke_kick_fires_after_delay():
    """延迟窗口后按 1008/auth revoked 踢出全部连接（设计意图保留）。"""
    server = _fake_server()
    await headless_main.auth_revoke_impl(server)
    await asyncio.sleep(headless_main._KICK_DELAY_SECONDS + 0.25)
    server.connection_manager.close_all.assert_awaited_once_with(code=1008, message=b"auth revoked", restart_heartbeat=True)


@pytest.mark.asyncio
async def test_revoke_rotate_false_skips_token_rotation():
    """rotate=False 时只吊销会话，不轮换主 token。"""
    server = _fake_server()
    result = await headless_main.auth_revoke_impl(server, rotate=False)
    assert result == {"success": True, "revoked": True, "rotated": False}
    server.rotate_auth_token.assert_not_called()
    server.revoke_all.assert_called_once_with()


@pytest.mark.asyncio
async def test_revoke_failure_returns_clean_dict():
    """revoke_all 失败 → success=False 结构化返回，且不调度踢连接。"""
    server = _fake_server()
    server.revoke_all = MagicMock(side_effect=RuntimeError("boom"))
    result = await headless_main.auth_revoke_impl(server)
    assert result["success"] is False
    assert "boom" in result["error"]
    server.connection_manager.close_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotate_failure_returns_clean_dict_and_no_kick():
    """rotate=True 且轮换失败 → success=False，不吊销、不踢连接。"""
    server = _fake_server()
    server.rotate_auth_token = MagicMock(side_effect=RuntimeError("boom"))
    result = await headless_main.auth_revoke_impl(server)
    assert result["success"] is False
    assert "boom" in result["error"]
    server.revoke_all.assert_not_called()
    server.connection_manager.close_all.assert_not_awaited()
