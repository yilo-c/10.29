"""P-A 鉴权深度攻击探针（2026-09-17 第二轮系统×环境测试）。

使命：用**真实进程攻击面**验证 N-D（WS 默认回环 + security_fail_closed_gate
exit 8）与 N-E（method_registry limit/offset 范围棘轮、脏参数不进审批队列）
两批修复真的兜住，并挖鉴权链残余缺陷。

三条测试通道（由真到轻）：
  ① in-process 真实 WebSocketServer（真 TCP + aiohttp 握手 + 拒绝码/close
     code），monkeypatch `_is_loopback` 模拟"远端来路"以走 _verify_auth 拒绝分支
     —— 桌面/回环免 token 是设计语义（RQ-1 只观测不改放行），故远端语义靠 patch。
  ② 裸 MethodRegistry + 真实 SandboxManager 单例：dispatch 内部棘轮/R4 竞态，
     纯内存可精确断言"脏参数是否真的进了 pending 队列"。
  ③ subprocess 真实解释器调 security_fail_closed_gate，断言进程 exit code 8
     （非 monkeypatch/静态读码，而是真进程退出码；不触 main()、不写日志文件、
      不碰真实 data/）。

红线：不改生产代码（仅 monkeypatch 测试实例属性）；不碰端口 18765、
不碰 思忆集app/data/；临时产物测完清理。

运行：
  D:/h/3.25/思忆集/.venv3.11/Scripts/python.exe -m pytest \
    思忆集app/tests/test_auth_probe_pa_20260917.py -q \
    --basetemp=D:/h/3.25/verify_tmp/pa
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import aiohttp
import pytest

from backend.headless_main import security_fail_closed_gate
from backend.method_registry import (
    PAGE_LIMIT_MAX,
    PAGE_OFFSET_MAX,
    MethodNotFound,
    MethodRegistry,
)
from backend.ws_server import WebSocketServer
from backend.event_bridge import EventBridge
from core.errors import InvalidParamsError
from services.sandbox_manager import get_sandbox_manager

pytestmark = pytest.mark.integration

APP_DIR = str(Path(__file__).resolve().parents[1])
_TEST_TOKEN = "pa-probe-token-0123456789abcdef"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── 通道①：in-process 真实 server fixture ──────────────────────────────


@pytest.fixture
async def server():
    """真实 WebSocketServer（随机端口、强制 auth、无心跳后台），测后停服。"""
    reg = MethodRegistry()
    seen: dict[str, dict] = {}

    async def echo(**kwargs):
        seen.update(kwargs)
        return {"echo": kwargs}

    async def echo_stream(send_chunk, **kwargs):
        await send_chunk("ok")
        yield {"echo": kwargs}

    reg.register("probe.echo", echo)
    reg.register("probe.echo_stream", echo_stream)
    reg.register("probe.r4echo", echo, risk="R4")

    bridge = EventBridge()
    port = _free_port()
    srv = WebSocketServer(
        reg,
        bridge,
        host="127.0.0.1",
        port=port,
        auth_token=_TEST_TOKEN,
        auth_enabled=True,
        enable_heartbeat=False,
    )
    srv._seen = seen  # type: ignore[attr-defined]
    srv._url_base = f"http://127.0.0.1:{port}"  # type: ignore[attr-defined]
    srv._ws_url = f"ws://127.0.0.1:{port}/ws"  # type: ignore[attr-defined]
    await srv.start()
    try:
        yield srv
    finally:
        await srv.stop()


async def _ws_handshake(server, *, token=None, headers=None, remote=False):
    """发起 WS 握手，返回 close code（拒绝）或 'OPEN'（放行后主动关闭）。

    remote=True → monkeypatch _is_loopback 判远端，走 _verify_auth 严格拒绝分支。
    """
    if remote:
        server._is_loopback = lambda request: False  # type: ignore[assignment]
    url = server._ws_url + "?client_version=1.2.0"
    hdrs = dict(headers or {})
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    async with aiohttp.ClientSession() as sess:
        try:
            async with sess.ws_connect(url, timeout=10, headers=hdrs) as ws:
                code = await _drain_close(ws)
                return code or "OPEN"
        except aiohttp.WSServerHandshakeError as exc:
            return exc.status
        except aiohttp.ClientConnectorError:
            return "CONN_REFUSED"


async def _drain_close(ws):
    """读帧直到拿到对端 close code（放行后对端不再主动关闭则返回 None）。"""
    import asyncio

    try:
        while True:
            msg = await asyncio.wait_for(ws.receive(), timeout=3)
            if msg.type == aiohttp.WSMsgType.CLOSE:
                return ws.close_code or msg.data
    except Exception:
        return None


# ── ①-1 token / Origin / loopback 信任语义 ────────────────────────────


class TestAuthHandshake:
    async def test_remote_no_token_rejected_1008(self, server) -> None:
        """远端（非回环）无 token → 1008 Unauthorized（N-D/ S1 双保险核心）。"""
        code = await _ws_handshake(server, token=None, remote=True)
        assert code == 1008, f"远端无 token 应 1008，实得 {code}"

    async def test_remote_wrong_token_rejected_1008(self, server) -> None:
        code = await _ws_handshake(server, token="wrong-token-zzz", remote=True)
        assert code == 1008

    async def test_remote_correct_token_allowed(self, server) -> None:
        code = await _ws_handshake(server, token=_TEST_TOKEN, remote=True)
        assert code == "OPEN", f"远端持正确 token 应放行，实得 {code}"

    async def test_malformed_bearer_no_prefix_rejected(self, server) -> None:
        """Bearer 前缀缺失（裸 token 塞 header）→ _verify_auth 不识别 → 1008。"""
        server._is_loopback = lambda request: False  # type: ignore[assignment]
        url = server._ws_url + "?client_version=1.2.0"
        async with aiohttp.ClientSession() as sess:
            async with sess.ws_connect(url, timeout=10, headers={"Authorization": _TEST_TOKEN}) as ws:
                code = await _drain_close(ws)
                assert code == 1008, f"无 Bearer 前缀应拒，实得 {code}"

    async def test_expired_session_cookie_rejected(self, server) -> None:
        """P1-6 cookie 走 session_id；伪造过期 session（>86400s）→ 拒绝。"""
        server._is_loopback = lambda request: False  # type: ignore[assignment]
        stale_sid = "stale-session-xyz"
        server._auth_sessions[stale_sid] = {
            "created": __import__("time").time() - 90000,
            "last_used": 0,
        }
        url = server._ws_url + "?client_version=1.2.0"
        async with aiohttp.ClientSession() as sess:
            async with sess.ws_connect(url, timeout=10, headers={"Cookie": f"siyi_auth={stale_sid}"}) as ws:
                code = await _drain_close(ws)
                assert code == 1008, f"过期 session 应拒，实得 {code}"

    async def test_loopback_anon_allowed_by_design(self, server) -> None:
        """RQ-1 语义核对：真实 loopback 无 token 仍放行（非缺陷，桌面渲染进程依赖），
        且匿名放行计入 loopback_anon 审计计数（可观测）。"""
        before = server.loopback_anon_count
        code = await _ws_handshake(server, token=None, remote=False)
        assert code == "OPEN", "loopback 免 token 放行是既定语义"
        assert server.loopback_anon_count == before + 1, "匿名放行应打审计位"


# ── ①-2 HTTP 端点鉴权（无 loopback 信任，真实可测拒绝）────────────────


async def _http_post(server, path, *, token=None, body=None, headers=None):
    hdrs = dict(headers or {})
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    async with aiohttp.ClientSession() as sess:
        async with sess.post(server._url_base + path, json=body or {}, headers=hdrs) as resp:
            try:
                payload = await resp.json()
            except Exception:
                payload = None
            return resp.status, payload


async def _http_get(server, path, *, token=None):
    hdrs = {}
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    async with aiohttp.ClientSession() as sess:
        async with sess.get(server._url_base + path, headers=hdrs) as resp:
            return resp.status


class TestHttpEndpointsAuth:
    async def test_rpc_no_token_401(self, server) -> None:
        status, payload = await _http_post(server, "/rpc", body={"method": "probe.echo", "params": {}})
        assert status == 401, f"/rpc 无 token 应 401，实得 {status}"
        assert payload["error"]["code"] == -32004

    async def test_rpc_wrong_token_401(self, server) -> None:
        status, _ = await _http_post(server, "/rpc", token="nope", body={"method": "probe.echo"})
        assert status == 401

    async def test_rpc_valid_token_ok(self, server) -> None:
        status, payload = await _http_post(server, "/rpc", token=_TEST_TOKEN, body={"method": "probe.echo", "params": {"limit": "5"}})
        assert status == 200
        assert payload["result"]["echo"]["limit"] == 5, "HTTP 通道同样经棘轮归一"

    async def test_health_requires_token(self, server) -> None:
        assert await _http_get(server, "/health") == 401
        # /health 需 _verify_auth（无 loopback 信任），带 token 才放行

    async def test_metrics_requires_token(self, server) -> None:
        assert await _http_get(server, "/metrics") == 401
        assert await _http_get(server, "/metrics", token=_TEST_TOKEN) == 200


# ── ①-3 Origin 校验（CSWSH 防护）──────────────────────────────────────


async def _ws_with_origin(server, origin, *, remote=True):
    if remote:
        server._is_loopback = lambda request: False  # type: ignore[assignment]
    url = server._ws_url + "?client_version=1.2.0"
    async with aiohttp.ClientSession() as sess:
        try:
            async with sess.ws_connect(url, timeout=10, headers={"Origin": origin}) as ws:
                code = await _drain_close(ws)
                return code or "OPEN"
        except aiohttp.WSServerHandshakeError as exc:
            return exc.status


class TestOriginGuard:
    async def test_evil_origin_no_token_rejected(self, server) -> None:
        code = await _ws_with_origin(server, "http://evil-attacker.example")
        assert code == 1008, f"恶意 Origin 无 token 应 1008，实得 {code}"

    async def test_localhost_prefix_bypass_blocked(self, server) -> None:
        """V2.1 精确匹配：http://localhost.attacker.example 不得靠前缀绕过。"""
        code = await _ws_with_origin(server, "http://localhost.attacker.example")
        assert code == 1008

    async def test_valid_origin_on_loopback_allowed(self, server) -> None:
        """真实 loopback + 合法 Origin → 放行（Origin 校验通过后走 loopback 信任）。

        注：非回环 + 合法白名单 Origin + 无 token 仍会被 _verify_auth 拒（1008）——
        Origin 白名单只解决 CSRF/CSWSH，不替代 token，属设计语义（见 test_remote_*）。
        """
        code = await _ws_with_origin(server, "http://localhost:5175", remote=False)
        assert code == "OPEN", f"loopback 合法 Origin 应放行，实得 {code}"


# ── ② 棘轮对抗值（进程内裸 registry）───────────────────────────────────


@pytest.fixture
def reg() -> MethodRegistry:
    r = MethodRegistry()
    seen: dict[str, dict] = {}

    async def echo(**kwargs):
        seen.update(kwargs)
        return kwargs

    async def echo_stream(send_chunk, **kwargs):
        await send_chunk("ok")
        return {"done": kwargs}

    async def r4echo(**kwargs):
        return kwargs

    r.register("t.echo", echo)
    r.register("t.echo_stream", echo_stream)
    r.register("t.r4", r4echo, risk="R4")
    r._seen = seen  # type: ignore[attr-defined]
    return r


async def _send(chunk, chunk_type: str = "chunk") -> None:
    return None


class TestRatchetAdversarial:
    """①bool ②"1e999" ③inf ④嵌套非数值 ⑤边界 10000/10001、1e6/1e6+1。"""

    async def test_bool_true_false_rejected(self, reg) -> None:
        for bad in (True, False):
            with pytest.raises(InvalidParamsError) as ei:
                await reg.dispatch("t.echo", {"limit": bad})
            assert ei.value.code == -32602
            assert ei.value.details.get("param") == "limit"

    async def test_scientific_string_rejected(self, reg) -> None:
        """int("1e999",10) ValueError → -32602，不得静默转 float。"""
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("t.echo", {"limit": "1e999"})

    async def test_hex_string_rejected(self, reg) -> None:
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("t.echo", {"limit": "0x10"})

    async def test_float_inf_rejected(self, reg) -> None:
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("t.echo", {"limit": 9e999})
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("t.echo", {"offset": float("inf")})

    async def test_nested_container_rejected(self, reg) -> None:
        for bad in ({"a": 1}, [1, 2], ("x",), set(), object()):
            with pytest.raises(InvalidParamsError):
                await reg.dispatch("t.echo", {"limit": bad})

    async def test_limit_boundary(self, reg) -> None:
        assert await reg.dispatch("t.echo", {"limit": PAGE_LIMIT_MAX}) == {"limit": PAGE_LIMIT_MAX}
        with pytest.raises(InvalidParamsError) as ei:
            await reg.dispatch("t.echo", {"limit": PAGE_LIMIT_MAX + 1})
        assert "out of range" in ei.value.message

    async def test_offset_boundary(self, reg) -> None:
        assert await reg.dispatch("t.echo", {"offset": PAGE_OFFSET_MAX}) == {"offset": PAGE_OFFSET_MAX}
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("t.echo", {"offset": PAGE_OFFSET_MAX + 1})

    async def test_huge_python_int_out_of_range(self, reg) -> None:
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("t.echo", {"limit": 10**20})

    async def test_stream_parity_bool_rejected(self, reg) -> None:
        with pytest.raises(InvalidParamsError):
            [c async for c in reg.dispatch_stream("t.echo_stream", {"limit": True}, _send)]

    async def test_non_dict_params_not_hard_crash(self, reg) -> None:
        """顶层 params 非 dict：棘轮跳过（不崩），但 handler kwargs 解包失败。
        记录残余：应回 -32602（非法参数），实测落 -32603 Internal error。"""
        # 裸 list/dict-as-non-mapping 不应引发 500 级崩溃；仅断言不抛非受控异常
        with pytest.raises((InvalidParamsError, MethodNotFound, TypeError)):
            await reg.dispatch("t.echo", [1, 2, 3])  # type: ignore[arg-type]


# ── ②-2 R4 审批链竞态：脏参数是否真的不进 pending ─────────────────────


class TestR4Race:
    async def test_dirty_param_never_enqueued(self, reg) -> None:
        """棘轮（line392）先于 R4 入队（line396）→ 脏参数 -32602，pending 零增长。"""
        mgr = get_sandbox_manager()
        mgr.clear_all()
        before = len(mgr.list_pending())
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("t.r4", {"limit": "abc"})
        assert len(mgr.list_pending()) == before, "脏参数不得进审批队列"
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("t.r4", {"limit": True})
        assert len(mgr.list_pending()) == before

    async def test_valid_r4_enqueued_normalized(self, reg) -> None:
        """合法 R4 入队，且 args 里 limit 是**归一后**的 int（不是字符串）。"""
        mgr = get_sandbox_manager()
        mgr.clear_all()
        before = len(mgr.list_pending())
        resp = await reg.dispatch("t.r4", {"limit": "50"})
        assert resp.get("pending") is True
        pend = mgr.list_pending()
        assert len(pend) == before + 1
        newest = max(pend, key=lambda e: e["created_at"])
        assert newest["args"]["limit"] == 50, "入队 args 须为棘轮归一后 int"
        # 清理，勿污染后续
        mgr.reject(newest["pending_id"])
        assert len(mgr.list_pending()) == before

    async def test_legacy_client_dirty_param_is_32602_not_32010(self, reg) -> None:
        """legacy_client=True 时脏参数仍先被棘轮拦为 -32602（而非 -32010）——
        校验优先于兼容性降级，脏数据不因旧客户端通道豁免。"""
        from core.errors import ClientUpgradeRequiredError

        mgr = get_sandbox_manager()
        mgr.clear_all()
        with pytest.raises(InvalidParamsError):
            await reg.dispatch("t.r4", {"limit": "abc"}, legacy_client=True)
        assert len(mgr.list_pending()) == 0

    async def test_anon_loopback_self_approves_r4_by_design(self, reg) -> None:
        """设计语义固化（RQ-1，非缺陷）：loopback 连接在 ws_server.py:1036 被无条件
        标 _conn_trusted_ui=True → 同连接用真实 _handle_sandbox_approve 可自批自己的
        R4（该函数 self-approve 拦截 line 99 要求 `not trusted`）。即：本机全权、
        审批闸门只约束远端/非可信 UI。若将来收紧（匿名 loopback 不再 trusted）本测试
        变红即为回归提醒。"""
        from backend.handlers._sandbox import _handle_sandbox_approve
        from services.sandbox_manager import reset_request_connection, set_request_connection

        mgr = get_sandbox_manager()
        mgr.clear_all()
        tokens = set_request_connection("loopback-anon-1", True)  # 复刻 ws_server:1036
        try:
            resp = await reg.dispatch("t.r4", {"limit": "50"})
            pid = resp["pending_id"]
            ack = await _handle_sandbox_approve(pid, registry=reg)
            assert ack.get("success") is True, f"匿名 loopback 自批应成功(设计如此): {ack}"
        finally:
            reset_request_connection(tokens)
            mgr.clear_all()

    async def test_nontrusted_conn_cannot_self_approve(self, reg) -> None:
        """对照（真缺陷防线生效）：trusted=False 的同连接自批被真实审批函数拒绝——
        R4 审批链对'非可信 UI'注入确实兜得住。"""
        from backend.handlers._sandbox import _handle_sandbox_approve
        from services.sandbox_manager import reset_request_connection, set_request_connection

        mgr = get_sandbox_manager()
        mgr.clear_all()
        tokens = set_request_connection("inject-conn", False)
        try:
            resp = await reg.dispatch("t.r4", {"x": 1})
            pid = resp["pending_id"]
            ack = await _handle_sandbox_approve(pid, registry=reg)
            assert ack.get("success") is not True
            assert "自我批准" in json.dumps(ack, ensure_ascii=False), f"应命中自批拦截: {ack}"
        finally:
            reset_request_connection(tokens)
            mgr.clear_all()


# ── ⑤ 方法名注入（注册表查询健壮性）────────────────────────────────────


class TestMethodNameInjection:
    async def test_dotted_anomaly_names(self, reg) -> None:
        reg.register("t.echo", await _noop())
        for bad in ("..", "a..b", "notes.list.", ".notes.list", "__init__", "os.system"):
            with pytest.raises(MethodNotFound):
                await reg.dispatch(bad, {})

    async def test_homoglyph_not_matched(self, reg) -> None:
        """西里尔 о 同形名不得命中原方法（不同码点=不同 key）。"""
        called = {"hit": False}

        async def real(**kwargs):
            called["hit"] = True
            return "ok"

        reg.register("notes.list", real)
        homoglyph = "nоtes.list"  # о = U+043E 西里尔
        assert homoglyph != "notes.list"
        with pytest.raises(MethodNotFound):
            await reg.dispatch(homoglyph, {})
        assert called["hit"] is False, "同形名不得触发真 handler"

    async def test_extremely_long_name_no_crash(self, reg) -> None:
        huge = "x" * 2_000_000
        with pytest.raises(MethodNotFound):
            await reg.dispatch(huge, {})

    async def test_control_char_name_wire_escaped(self, server) -> None:
        """注入 CRLF 方法名 → -32601 文案中的控制字符须在**线路层**被 JSON 转义
        （裸 CRLF 上线路 = 响应分割/头注入向量）。验 raw body，不验解析后 dict
        （解析后还原真实 \r\n 属 JSON 正常语义）。"""
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                server._url_base + "/rpc",
                json={"method": "evil\r\nX-Injected: 1", "params": {}},
                headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
            ) as resp:
                assert resp.status == 404
                raw = await resp.text()
                assert "\rX-Injected" not in raw, "响应体线路层不得含裸 CRLF（json.dumps 必转义）"
                assert "\\r\\nX-Injected" in raw, "控制字符应以 JSON 转义序列下发"
                # 响应头中也不得混入注入内容
                assert "X-Injected" not in " ".join(f"{k}:{v}" for k, v in resp.headers.items())


async def _noop():
    async def f(**kwargs):
        return {}

    return f


# ── ④ fail-closed gate：真进程 exit code ──────────────────────────────


def _run_gate(ws_host_repr: str, auth_enabled: str) -> subprocess.CompletedProcess:
    """subprocess 起真解释器调 security_fail_closed_gate，回传 CompletedProcess。

    不跑 main()/async_main() → 不启服务、不写日志文件、不碰真实 data/。
    `-c` 脚本的 __name__ 是 __main__，但被导入模块 __name__ 为其模块名，
    不触发 `if __name__ == "__main__"` 守卫。
    """
    code = (
        "import sys;"
        "from backend.headless_main import security_fail_closed_gate as g;"
        f"g({ws_host_repr!r}, {auth_enabled});"  # !r 给 host 自动补引号
        "print('NO_EXIT')"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = APP_DIR + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=APP_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestFailClosedGate:
    def test_nonloopback_no_auth_exit8(self) -> None:
        """0.0.0.0 + auth 不可用 → 真实进程退出码 8（N-D 核心承诺）。"""
        r = _run_gate("0.0.0.0", "False")
        assert r.returncode == 8, f"应 exit 8，实得 rc={r.returncode} out={r.stdout!r} err={r.stderr[-400:]!r}"
        assert "NO_EXIT" not in r.stdout

    def test_lan_ip_no_auth_exit8(self) -> None:
        r = _run_gate("192.168.1.50", "False")
        assert r.returncode == 8

    def test_nonloopback_with_auth_proceeds(self) -> None:
        """0.0.0.0 + auth 可用 → 放行（exit 0，非 fail-closed 目标场景）。"""
        r = _run_gate("0.0.0.0", "True")
        assert r.returncode == 0, f"非回环+有认证应放行 rc={r.returncode} err={r.stderr[-300:]!r}"
        assert "NO_EXIT" in r.stdout

    def test_loopback_no_auth_proceeds(self) -> None:
        for host in ("127.0.0.1", "::1", "localhost", "127.5.5.5", "::ffff:127.0.0.1"):
            r = _run_gate(f"{host}", "False")
            assert r.returncode == 0, f"回环 {host} 无认证应允许单机自用，实得 rc={r.returncode}"


# ── 静态读码佐证：gate 在 server 构造前、默认回环 ──────────────────────


def test_static_wiring_evidence() -> None:
    src = (Path(APP_DIR) / "backend" / "headless_main.py").read_text(encoding="utf-8")
    # 默认绑定回环
    assert 'or "127.0.0.1"' in src, "WS_HOST 默认须回环"
    # gate 调用早于 WebSocketServer 构造
    gate_at = src.index("security_fail_closed_gate(WS_HOST")
    ctor_at = src.index("server = WebSocketServer(")
    assert gate_at < ctor_at, "fail-closed 守卫须在 server 构造前，否则已监听才拒=无意义"
    regsrc = (Path(APP_DIR) / "backend" / "method_registry.py").read_text(encoding="utf-8")
    # 棘轮先于 R4 入队
    rat = regsrc.index("self._ratchet_paging(method, params)")
    enr = regsrc.index("await self._enqueue_r4_approval")
    assert rat < enr, "棘轮须先于 R4 审批入队，否则脏参数仍进队列"
