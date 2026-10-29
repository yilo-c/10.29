"""M2.1-6 R4 向后兼容：旧客户端（未声明 client_version）调 R4 → 显式 -32010。

出口断言（任务书 M2.1-6）：
①无 client_version 连接调 notes.delete → 收 -32010 而非 {pending:true}；
②声明 client_version 的连接调同方法 → 收 pending 信封；
③-32010 定义存在于 core/errors.py。

隔离：MagicMock bridge / fake sandbox，不触真队列；_on_message 直驱 + fake ws。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.method_registry import RISK_R4, MethodRegistry
from backend.ws_server import WebSocketServer
from core.errors import ClientUpgradeRequiredError, ErrorCode


def _make_server(registry):
    return WebSocketServer(
        registry,
        MagicMock(),
        host="127.0.0.1",
        port=0,
        enable_heartbeat=False,
    )


def _r4_registry():
    reg = MethodRegistry()

    async def spy_delete(note_id: str = "", **kwargs: object) -> dict:
        return {"deleted": note_id}

    reg.register("notes.delete", spy_delete, risk=RISK_R4)
    return reg


def _fake_ws():
    ws = MagicMock()
    ws.closed = False
    ws.send_str = AsyncMock()
    return ws


def _sent_payload(ws):
    assert ws.send_str.await_count >= 1
    return json.loads(ws.send_str.call_args[0][0])


@pytest.mark.asyncio
async def test_legacy_client_gets_explicit_minus32010_not_pending() -> None:
    """①未声明 client_version 的连接：R4 → JSON-RPC error -32010，非 pending。"""
    fake_sandbox = MagicMock()
    fake_sandbox.add.return_value = "pid-x"
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake_sandbox):
        server = _make_server(_r4_registry())
        ws = _fake_ws()
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "notes.delete", "params": {"note_id": "X"}})
        await server._on_message(ws, raw, conn_id="conn-legacy")
        sent = _sent_payload(ws)
    assert "error" in sent and "result" not in sent
    assert sent["error"]["code"] == -32010
    assert "请升级客户端" in sent["error"]["message"]
    fake_sandbox.add.assert_not_called()


@pytest.mark.asyncio
async def test_versioned_client_still_gets_pending() -> None:
    """②声明 client_version 的连接走原 pending 流。"""
    fake_sandbox = MagicMock()
    fake_sandbox.add.return_value = "pid-y"
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake_sandbox):
        server = _make_server(_r4_registry())
        server._conn_client_version["conn-modern"] = "3.25.0"  # 模拟握手 query 声明
        ws = _fake_ws()
        raw = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "notes.delete", "params": {"note_id": "Y"}})
        await server._on_message(ws, raw, conn_id="conn-modern")
        sent = _sent_payload(ws)
    assert "result" in sent and "error" not in sent
    assert sent["result"]["pending"] is True
    fake_sandbox.add.assert_called_once()


@pytest.mark.asyncio
async def test_first_frame_declaration_upgrades_connection() -> None:
    """②b首帧字段声明 client_version → 同帧即走 pending（连接级最小识别）。"""
    fake_sandbox = MagicMock()
    fake_sandbox.add.return_value = "pid-z"
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake_sandbox):
        server = _make_server(_r4_registry())
        ws = _fake_ws()
        raw = json.dumps(
            {"jsonrpc": "2.0", "id": 3, "client_version": "3.26.0", "method": "notes.delete", "params": {}}
        )
        await server._on_message(ws, raw, conn_id="conn-declare")
        sent = _sent_payload(ws)
    assert sent.get("result", {}).get("pending") is True


def test_error_code_32010_defined_in_core_errors() -> None:
    """③-32010 定义存在于 core/errors.py（常量 + 错误类）。"""
    assert ErrorCode.CLIENT_UPGRADE_REQUIRED == -32010
    assert ClientUpgradeRequiredError.code == -32010
    assert "请升级客户端" in ClientUpgradeRequiredError.message
    src = (APP_ROOT / "core" / "errors.py").read_text(encoding="utf-8")
    assert "-32010" in src
