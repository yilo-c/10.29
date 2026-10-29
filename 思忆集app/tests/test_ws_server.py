"""WebSocketServer (backend/ws_server.py) 单元测试。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from backend.ws_server import WebSocketServer
from core.errors import ErrorCode


@pytest.fixture
def registry():
    """返回带有异步 dispatch 的 MethodRegistry 替身。"""
    m = MagicMock()
    m.dispatch = AsyncMock(return_value={"ok": True})
    m.dispatch_stream = AsyncMock(return_value=_empty_async_iter())
    return m


@pytest.fixture
def bridge():
    """返回 EventBridge 替身。"""
    return MagicMock()


def _empty_async_iter():
    async def _gen():
        if False:
            yield None

    return _gen()


def _make_server(registry, bridge, *, auth_enabled: bool = False, auth_token: str | None = None):
    """构造一个用于测试的 WebSocketServer（不启动真实端口）。"""
    return WebSocketServer(
        registry,
        bridge,
        host="127.0.0.1",
        port=0,
        enable_heartbeat=False,
        auth_enabled=auth_enabled,
        auth_token=auth_token,
    )


@pytest.mark.asyncio
async def test_health_endpoint_returns_status(registry, bridge):
    server = _make_server(registry, bridge)
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert "status" in data


@pytest.mark.asyncio
async def test_stats_endpoint_returns_stats_json(registry, bridge):
    server = _make_server(registry, bridge)
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/stats")
        assert resp.status == 200
        data = await resp.json()
        assert "active" in data
        assert "max" in data
        assert "total" in data
        assert "uptime_seconds" in data
        assert data.get("version") == "1.0.0"
        assert "performance" in data


@pytest.mark.asyncio
async def test_auth_endpoint_returns_400_for_invalid_json(registry, bridge):
    server = _make_server(registry, bridge)
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.post("/auth", data="not-json")
        assert resp.status == 400
        data = await resp.json()
        assert data.get("error") == "Invalid JSON"


@pytest.mark.asyncio
async def test_auth_endpoint_returns_401_for_invalid_token(registry, bridge):
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.post("/auth", json={"token": "bad-token"})
        assert resp.status == 401
        data = await resp.json()
        assert data.get("error") == "Invalid token"


@pytest.mark.asyncio
async def test_auth_endpoint_returns_200_with_session_id_and_cookie_for_valid_token(registry, bridge):
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.post("/auth", json={"token": "secret"})
        assert resp.status == 200
        data = await resp.json()
        assert data.get("success") is True
        assert "session_id" in data
        assert "siyi_auth" in resp.cookies


@pytest.mark.asyncio
async def test_rpc_endpoint_returns_401_when_auth_enabled_and_no_token(registry, bridge):
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.post("/rpc", json={"method": "ping", "params": {}, "id": 1})
        assert resp.status == 401
        data = await resp.json()
        assert data.get("jsonrpc") == "2.0"
        assert data["error"]["code"] == ErrorCode.UNAUTHORIZED


@pytest.mark.asyncio
async def test_rpc_endpoint_dispatches_method_and_returns_result_when_auth_disabled(registry, bridge):
    registry.dispatch.return_value = {"result": "pong"}
    server = _make_server(registry, bridge)
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.post("/rpc", json={"method": "ping", "params": {"x": 1}, "id": 42})
        assert resp.status == 200
        data = await resp.json()
        assert data.get("jsonrpc") == "2.0"
        assert data.get("id") == 42
        assert data.get("result") == {"result": "pong"}
        # A-1：HTTP /rpc 按 peer IP 分桶限流（aiohttp TestClient 来源为 "127.0.0.1"）
        # M2.1 F1：HTTP 与 WS 同源判定 legacy_client——请求体未声明 client_version → True
        registry.dispatch.assert_awaited_once_with("ping", {"x": 1}, client_id="127.0.0.1", legacy_client=True)


@pytest.mark.asyncio
async def test_websocket_connection_rejected_when_auth_enabled_and_no_token(registry, bridge):
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    request = MagicMock(spec=web.Request)
    request.query = {}
    request.headers = {}
    request.cookies = {}
    request.remote = "127.0.0.1"

    ws_mock = MagicMock()
    ws_mock.prepare = AsyncMock()
    ws_mock.close = AsyncMock()

    with patch("backend.ws_server.web.WebSocketResponse", return_value=ws_mock):
        result = await server._handle_websocket(request)

    assert result is ws_mock
    ws_mock.prepare.assert_awaited_once_with(request)
    ws_mock.close.assert_awaited_once_with(code=1008, message=b"Unauthorized")


@pytest.mark.asyncio
async def test_websocket_connection_accepted_when_auth_disabled(registry, bridge):
    server = _make_server(registry, bridge)
    async with TestClient(TestServer(server._app)) as client:
        async with client.ws_connect("/ws") as ws:
            # 连接应处于打开状态
            assert ws.closed is False
        # 客户端关闭后，服务端应完成清理并正常关闭
        assert ws.close_code is not None


# ── V3-01 URL token 通道契约（批次1#4 更新：手机端经 ?token= 连 WS）──


def _auth_request(*, query=None, headers=None, cookies=None):
    request = MagicMock(spec=web.Request)
    request.query = query or {}
    request.headers = headers or {}
    request.cookies = cookies or {}
    request.remote = "127.0.0.1"
    return request


@pytest.mark.asyncio
async def test_url_token_accepted_for_remote_mobile(registry, bridge, monkeypatch):
    """批次1#4：远端手机持真实主 token 走 ?token= 连 WS 是唯一可行通道（浏览器 WS
    无法带 header/cookie），故默认即放行有效主 token（持有者凭证与 Bearer 等价）。"""
    monkeypatch.delenv("SIYI_WS_ALLOW_URL_TOKEN", raising=False)
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    request = _auth_request(query={"token": "secret"})

    assert server._verify_auth(request) is True


@pytest.mark.asyncio
async def test_url_token_rejected_when_invalid(registry, bridge, monkeypatch):
    """?token= 无效（非主 token）必须被拒——URL 通道不放行伪造凭证。"""
    monkeypatch.delenv("SIYI_WS_ALLOW_URL_TOKEN", raising=False)
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    request = _auth_request(query={"token": "wrong"})

    assert server._verify_auth(request) is False


@pytest.mark.asyncio
async def test_verify_auth_header_channel_works_by_default(registry, bridge, monkeypatch):
    """V3-01 不得误伤主认证通道：默认配置下 Authorization: Bearer 仍放行。"""
    monkeypatch.delenv("SIYI_WS_ALLOW_URL_TOKEN", raising=False)
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    request = _auth_request(headers={"Authorization": "Bearer secret"})

    assert server._verify_auth(request) is True


# ── P2-4 回归测试：错误响应绝不携带 traceback / 路径 / 内部状态 ──


def _assert_no_traceback_leak(payload: str) -> None:
    """断言任何形式的 payload 都不应包含服务端 traceback 痕迹。"""
    assert "Traceback" not in payload
    assert 'File "' not in payload
    # 常见模块路径片段（traceback 中典型出现）
    assert "\\backend\\ws_server" not in payload
    assert "/backend/ws_server" not in payload


@pytest.mark.asyncio
async def test_rpc_http_error_response_does_not_contain_traceback(registry, bridge):
    """P2-4: /rpc 路径的内部错误响应不得回传 traceback。"""
    registry.dispatch = AsyncMock(side_effect=RuntimeError("boom at /home/user/secret.py"))
    server = _make_server(registry, bridge)
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.post("/rpc", json={"method": "x", "params": {}, "id": 7})
        assert resp.status == 500
        raw = await resp.text()
        _assert_no_traceback_leak(raw)
        data = await resp.json()
        assert data["error"]["code"] == -32603
        # message 来自 str(exc)，允许；但 data 字段不应存在
        assert "data" not in data["error"] or data["error"].get("data") is None


@pytest.mark.asyncio
async def test_error_middleware_does_not_leak_traceback_when_debug_off():
    """P2-4: create_error_middleware(debug=False) 响应不含 traceback。"""
    from core.errors import create_error_middleware

    async def boom(request: web.Request) -> web.Response:
        raise RuntimeError("internal path /etc/passwd leaked")

    app = web.Application(middlewares=[create_error_middleware(debug=False)])
    app.router.add_get("/boom", boom)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/boom")
        assert resp.status == 500
        raw = await resp.text()
        _assert_no_traceback_leak(raw)
        data = await resp.json()
        # debug=False：error.data 仅含通用 message
        assert "traceback" not in (data.get("error", {}).get("data") or {})
        assert "/etc/passwd" not in raw


@pytest.mark.asyncio
async def test_error_middleware_debug_mode_only_exposes_exception_name():
    """P2-4: 即使 debug=True，也仅暴露异常类名 + 截断消息，绝不回传 traceback。"""
    from core.errors import create_error_middleware

    async def boom(request: web.Request) -> web.Response:
        raise ValueError("a" * 500)  # 长 message，验证截断

    app = web.Application(middlewares=[create_error_middleware(debug=True)])
    app.router.add_get("/boom", boom)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/boom")
        assert resp.status == 500
        raw = await resp.text()
        assert "Traceback" not in raw
        assert 'File "' not in raw
        data = await resp.json()
        edata = data["error"]["data"]
        assert edata.get("exception") == "ValueError"
        # detail 必须被截断到 ≤ 200
        assert len(edata.get("detail", "")) <= 200
        assert "traceback" not in edata


def test_error_from_exception_never_leaks_traceback():
    """P2-4: error_from_exception 即便 debug=True 也不回传 traceback。"""
    from core.errors import error_from_exception

    try:
        raise RuntimeError("kaboom from /opt/app/secret.py")
    except RuntimeError as e:
        result_debug = error_from_exception(e, debug=True)
        result_prod = error_from_exception(e, debug=False)

    # 生产模式：无 data
    assert "data" not in result_prod or result_prod.get("data") is None

    # debug 模式：仅 exception + detail，无 traceback / 路径
    payload_debug = json.dumps(result_debug)
    assert "Traceback" not in payload_debug
    assert 'File "' not in payload_debug
    assert "/opt/app/" in payload_debug  # 路径在 message 里允许（str(exc)），
    # 但不应该在 data["traceback"] 中
    assert "traceback" not in (result_debug.get("data") or {})
    assert result_debug["data"]["exception"] == "RuntimeError"


# ── P2-14 回归测试：流式断连用 asyncio.Event 而非 50ms 轮询 ──


@pytest.mark.asyncio
async def test_stream_completes_normally_via_event_path(registry, bridge):
    """P2-14: 正常完成路径 — dispatch_stream 跑完后服务端发送 done 帧。"""
    import asyncio

    async def _gen(method, params, send_chunk):
        await send_chunk("hello")
        await send_chunk("world")
        if False:  # pragma: no cover — 让本函数成为 async generator
            yield None

    registry.dispatch_stream = lambda method, params, send_chunk, **kwargs: _gen(method, params, send_chunk)

    server = _make_server(registry, bridge)
    async with TestClient(TestServer(server._app)) as client:
        async with client.ws_connect("/ws") as ws:
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "chat.send",
                    "params": {"message": "hi"},
                    "stream": True,
                }
            )

            chunks: list[dict] = []
            saw_done = False
            for _ in range(10):
                msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                result = msg.get("result", {})
                if result.get("type") == "chunk":
                    chunks.append(result)
                elif result.get("type") == "done":
                    saw_done = True
                    break

            assert saw_done is True
            assert [c["data"] for c in chunks] == ["hello", "world"]


@pytest.mark.asyncio
async def test_stream_cancels_promptly_on_client_disconnect(registry, bridge):
    """P2-14: 客户端断开后，慢速生成任务应被快速取消（< 2s 兜底）。

    旧实现每 50ms 轮询，理论最大延迟 50ms。新实现走 send_chunk 触发的 asyncio.Event，
    应该在第一次 send 失败的同帧或 500ms watchdog 内取消。
    """
    import asyncio
    import time

    cancelled = asyncio.Event()

    async def _slow_gen(method, params, send_chunk):
        try:
            for i in range(100):
                await send_chunk(f"tick-{i}")
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except Exception:
            # send 失败 = 客户端已断开
            cancelled.set()
            raise
        if False:  # pragma: no cover
            yield None

    registry.dispatch_stream = lambda method, params, send_chunk, **kwargs: _slow_gen(method, params, send_chunk)

    server = _make_server(registry, bridge)
    async with TestClient(TestServer(server._app)) as client:
        ws = await client.ws_connect("/ws")
        await ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "chat.send",
                "params": {"message": "hi"},
                "stream": True,
            }
        )

        # 接收至少一个 chunk 证明流真的在跑
        msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
        assert msg.get("result", {}).get("type") == "chunk"

        # 客户端主动断开
        t0 = time.monotonic()
        await ws.close()

        # 等服务端发现并取消（兜底 watchdog 500ms + send_chunk 失败立即触发，二选一）
        await asyncio.wait_for(cancelled.wait(), timeout=2.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"stream cancellation took too long: {elapsed:.3f}s"


# ── V2.1 批次1#2：Origin 白名单精确匹配（防 CSWSH / CSRF / startswith 前缀绕过）──


@pytest.mark.parametrize(
    "origin,expected",
    [
        # 白名单内
        ("http://localhost:5175", True),
        ("http://127.0.0.1:5175", True),
        ("http://localhost:5190", True),
        ("http://127.0.0.1:5190", True),
        # file://（Electron 生产打包）
        ("file://", True),
        ("file:///path/to/index.html", True),
        # 前缀绕过攻击：http://localhost.attacker.com 不得匹配 http://localhost
        ("http://localhost.attacker.com", False),
        ("http://127.0.0.1.attacker.com", False),
        ("https://localhost.evil.io", False),
        # 非白名单端口/来源
        ("http://localhost:9999", False),
        ("http://evil.example.com", False),
        ("http://example.com", False),
        # 非法/空
        ("", False),
        ("not-a-url", False),
    ],
)
def test_is_localhost_origin_precise_matching(registry, bridge, origin, expected):
    server = _make_server(registry, bridge)
    assert server._is_localhost_origin(origin) is expected


@pytest.mark.asyncio
async def test_websocket_rejected_with_malicious_origin(registry, bridge):
    """V2.1 批次1#2 + CSWSH 回归：恶意 Origin 必须被拒，即使 transport 是 loopback。

    TestClient 连 127.0.0.1（transport 即本机 loopback），与本机浏览器里恶意网页
    连 ws://127.0.0.1 场景等价。历史上 loopback 无条件信任会让此类连接被放行
    （CSWSH）——此处确保 loopback transport 也不放行非白名单 Origin。
    """
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    async with TestClient(TestServer(server._app)) as client:
        ws = await client.ws_connect("/ws", headers={"Origin": "http://localhost.evil.com"})
        # 服务端在握手阶段就 close：receive 应返回 CLOSED，连接应为 closed 状态
        msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
        assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
        assert ws.closed is True


@pytest.mark.asyncio
async def test_websocket_rejected_with_evil_site_origin_from_loopback(registry, bridge):
    """CSWSH 回归：本机浏览器里恶意站点页面（transport loopback + Origin 为公网
    恶意域名）连 /ws 必须被拒——防恶意网页经浏览器直连本机后端读写数据。"""
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    async with TestClient(TestServer(server._app)) as client:
        ws = await client.ws_connect("/ws", headers={"Origin": "https://evil.example.com"})
        msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
        assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
        assert ws.closed is True


@pytest.mark.asyncio
async def test_websocket_accepted_with_electron_localhost_origin(registry, bridge):
    """Electron 渲染进程（本机 loopback + Origin http://localhost:5190）应能连上
    自身后端——loopback 信任仅对该类白名单 Origin 生效，不误伤桌面端。"""
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    async with TestClient(TestServer(server._app)) as client:
        ws = await client.ws_connect("/ws", headers={"Origin": "http://localhost:5190"})
        assert ws.closed is False


@pytest.mark.asyncio
async def test_rpc_http_rejected_with_malicious_origin(registry, bridge):
    """V2.1 批次1#2: /rpc 收到恶意 Origin 应返回 403。"""
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.post("/rpc", json={"method": "ping", "params": {}, "id": 1}, headers={"Origin": "http://localhost.evil.com"})
        assert resp.status == 403
        data = await resp.json()
        assert data["error"]["message"] == "Origin not allowed"


@pytest.mark.asyncio
async def test_metrics_requires_auth(registry, bridge):
    """V2.1 批次1#3: /metrics 未认证访问返回 401。"""
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.get("/metrics")
        assert resp.status == 401


# ── RQ-1（M1 后端A · 2026-09-12）：匿名 loopback 审计位 ──
# 实测：TestClient 与后端在 127.0.0.1 上真实建连 → transport peername 即 loopback，
# 与"本机 Electron/Node relay 无 token 直连"场景等价。放行语义**保持现状**（个人自用，
# 先观测后收紧），此处只锁死"必须留下 source=loopback_anon 审计痕迹"。


def _make_audited_server(registry, bridge, data_dir, *, auth_enabled: bool = True, auth_token: str | None = "secret"):
    """带显式数据目录的 WebSocketServer（审计位落盘到 data_dir/audit_trail.db）。"""
    return WebSocketServer(
        registry,
        bridge,
        host="127.0.0.1",
        port=0,
        enable_heartbeat=False,
        auth_enabled=auth_enabled,
        auth_token=auth_token,
        auth_token_store=str(data_dir),
    )


def _anon_audit_rows(writer) -> list[dict]:
    writer.drain()
    return [r for r in writer.fetch_integration(provider="backend.auth") if r["event"] == "auth.loopback_anon"]


@pytest.mark.asyncio
async def test_rq1_anonymous_loopback_ws_is_allowed_and_audited(registry, bridge, tmp_path, caplog):
    """RQ-1 现状 + 审计位：无 token/无 session 的 127.0.0.1 WS 连接仍被放行执行 RPC，
    但必须计数、落 integration_audit（source=loopback_anon）并 logger.warning。"""
    import logging

    registry.dispatch.return_value = {"pong": True}
    server = _make_audited_server(registry, bridge, tmp_path)
    with caplog.at_level(logging.WARNING, logger="siyi.backend.ws"):
        async with TestClient(TestServer(server._app)) as client:
            async with client.ws_connect("/ws") as ws:
                assert ws.closed is False, "放行语义不得改变：匿名 loopback 仍应连上"
                # 用非内置 ping 的方法，确保真的走 registry.dispatch（即"被放行执行"）
                await ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "notes.list", "params": {}})
                msg = await asyncio.wait_for(ws.receive_json(), timeout=3.0)
                assert msg.get("result") == {"pong": True}, "匿名 loopback 的 RPC 确实被执行了（RQ-1）"
    registry.dispatch.assert_awaited_once()

    assert server.loopback_anon_count == 1
    writer = server._resolve_audit_writer()
    assert writer is not None
    rows = _anon_audit_rows(writer)
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["source"] == "loopback_anon"
    assert payload["channel"] == "ws"
    assert payload["path"] == "/ws"
    assert payload["auth_enabled"] is True
    assert rows[0]["status"] == "loopback_anon"
    assert any("loopback_anon" in rec.getMessage() for rec in caplog.records), "必须打 logger.warning 告警"


@pytest.mark.asyncio
async def test_rq1_loopback_with_valid_token_is_not_marked(registry, bridge, tmp_path):
    """带有效主 token 的 loopback 连接不是匿名 → 不打审计位（观测口径要准）。"""
    server = _make_audited_server(registry, bridge, tmp_path)
    async with TestClient(TestServer(server._app)) as client:
        async with client.ws_connect("/ws?token=secret") as ws:
            assert ws.closed is False

    assert server.loopback_anon_count == 0
    assert _anon_audit_rows(server._resolve_audit_writer()) == []


@pytest.mark.asyncio
async def test_rq1_anonymous_loopback_http_rpc_is_audited(registry, bridge, tmp_path):
    """auth_enabled=False 时 _verify_auth 的 localhost 分支放行匿名 HTTP RPC → 同样打点。"""
    registry.dispatch.return_value = {"pong": True}
    server = _make_audited_server(registry, bridge, tmp_path, auth_enabled=False, auth_token=None)
    assert server.auth_enabled is False
    async with TestClient(TestServer(server._app)) as client:
        resp = await client.post("/rpc", json={"method": "ping", "params": {}, "id": 1})
        assert resp.status == 200

    assert server.loopback_anon_count == 1
    rows = _anon_audit_rows(server._resolve_audit_writer())
    assert rows and rows[0]["payload"]["channel"] == "http_rpc"


def test_rq1_presents_valid_credential_channels(registry, bridge, tmp_path):
    """凭证识别：Bearer / ?token= / cookie session 任一有效即非匿名；伪造凭证仍算匿名。"""
    import time

    server = _make_audited_server(registry, bridge, tmp_path)
    assert server._presents_valid_credential(_auth_request(headers={"Authorization": "Bearer secret"})) is True
    assert server._presents_valid_credential(_auth_request(query={"token": "secret"})) is True
    assert server._presents_valid_credential(_auth_request(query={"token": "forged"})) is False

    server._auth_sessions["sid-1"] = {"created": time.time(), "last_used": 0.0}
    assert server._presents_valid_credential(_auth_request(cookies={"siyi_auth": "sid-1"})) is True
    assert server._presents_valid_credential(_auth_request(cookies={"siyi_auth": "nope"})) is False
    assert server._presents_valid_credential(_auth_request()) is False


def test_rq1_audit_degrades_without_explicit_data_dir(registry, bridge, monkeypatch):
    """无显式数据目录（裸构造）→ 不落盘、不污染真实 data/，但计数与告警仍在。"""
    monkeypatch.delenv("SIYI_DATA_DIR", raising=False)
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    request = _auth_request()
    request.path = "/ws"
    server._mark_loopback_anon(request, channel="ws")

    assert server.loopback_anon_count == 1
    assert server._resolve_audit_writer() is None
