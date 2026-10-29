"""L8 兼容：WS JSON-RPC 协议契约 / 版本协商。

现状：WS 协议**没有应用层版本协商**——客户端与服务器只约定
JSONRPC ``"2.0"``，信封固定为 ``{jsonrpc, id, method, params}``，
无 per-client 协议版本字段、无握手步骤。

本文件把「无协商」固守为兼容性契约，断言：
- 标准信封：``jsonrpc: "2.0"`` + ``id`` 回显 + ``result`` / ``error``；
- 错误码：畸形 JSON → -32700、缺 method → -32600、未知 method → -32601；
- ``ping`` 心跳 → ``"pong"``；
- 不带版本字段的客户端请求照常被服务（不存在版本门禁/拒绝）。

集成级：模块级隔离后端（随机端口 + 临时数据目录），一次 spawn 复用。
"""
from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from _isolated_backend import rpc, spawn_backend, teardown_backend


@pytest.fixture(scope="module")
def backend() -> str:
    """模块级独立后端，返回 ws_url。"""
    proc, ws_url, data_dir = spawn_backend()
    yield ws_url
    teardown_backend(proc, data_dir)


async def _raw_frame(ws_url: str, raw_text: str) -> dict:
    """发送一条原始文本帧并读取响应（用于畸形/缺字段帧）。"""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url, timeout=10) as ws:
            await ws.send_str(raw_text)
            msg = await asyncio.wait_for(ws.receive(), timeout=10)
            if msg.type == aiohttp.WSMsgType.TEXT:
                return json.loads(msg.data)
            return {"error": {"code": -32000, "message": f"unexpected frame: {msg.type}"}}


class TestEnvelopeContract:
    def test_ping_returns_pong(self, backend: str) -> None:
        resp = asyncio.run(rpc(backend, "ping"))
        assert resp["result"] == "pong"

    def test_envelope_carries_jsonrpc_and_id(self, backend: str) -> None:
        resp = asyncio.run(rpc(backend, "ping"))
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 1

    def test_unknown_method_returns_minus32601(self, backend: str) -> None:
        resp = asyncio.run(rpc(backend, "no.such.method"))
        assert resp["error"]["code"] == -32601
        assert "no.such.method" in resp["error"]["message"]

    def test_missing_method_returns_minus32600(self, backend: str) -> None:
        resp = asyncio.run(
            _raw_frame(backend, json.dumps({"jsonrpc": "2.0", "id": 7, "params": {}}))
        )
        assert resp["error"]["code"] == -32600
        assert resp["id"] == 7

    def test_malformed_json_returns_minus32700(self, backend: str) -> None:
        resp = asyncio.run(_raw_frame(backend, "this is not json"))
        assert resp["error"]["code"] == -32700


class TestNoVersionGate:
    def test_request_without_version_field_is_served(self, backend: str) -> None:
        """客户端不发版本字段也能正常调用（不存在版本门禁）。"""
        resp = asyncio.run(rpc(backend, "ping"))
        assert resp.get("result") == "pong"
        assert "error" not in resp

    def test_system_info_reports_version_without_gating(self, backend: str) -> None:
        """system.info 报告软件版本，但仅作信息、不参与协商/拒绝。"""
        resp = asyncio.run(rpc(backend, "system.info"))
        result = resp.get("result")
        assert isinstance(result, dict)
        assert "version" in result
        assert "name" in result
