"""R2 组合攻击链矩阵（audit-report-2026-08-27 章节 4 R2 / 原方案 11.8 批次1#10 欠账）。

组合矩阵（认证 × 领域闸门）——验证「认证失败 → 任何领域不可达」的分层防御：
  1. 认证×审批：远端无 token → 认证闸门拒绝，R4 删除审批路径不可达；
     （R4 审批闸门本身由 test_p0_6_delete_r4_gating.py 独立覆盖）
  2. 认证×settings：远端无 token → 认证拒绝；持有效 token 后 settings.set
     敏感键返回脱敏值（认证 + 脱敏双层都成立）；
  3. 认证×cron：远端无 token → 认证拒绝；持有效 token 后 cron.add 可达、
     cron.remove 仍为 R4（域内风险分级不因认证通过而放松）；
  4. 水位×认证：云同步认证失败（Worker 拒绝 register/upload）→ 发 sync.failed
     fatal:True 事件 + stop_sync_loop()（循环停止，水位不推进，避免被 Worker 封禁）。

隔离：纯内存（MagicMock / register_all + 最小 context / CloudSyncClient 无网络），
不依赖真实后端进程与文件系统（沙箱可跑，与 test_p0_6 同风格）。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from backend.handlers import register_all
from backend.method_registry import RISK_R4, MethodRegistry
from backend.ws_server import WebSocketServer
from sync._cloud_sync_crypto import CloudSyncAuthError
from sync.cloud_sync_client import CloudSyncClient


# ── 认证闸门夹具（与 test_ws_server.py 同构） ──


@pytest.fixture
def registry():
    m = MagicMock()
    m.dispatch = AsyncMock(return_value={"ok": True})
    return m


@pytest.fixture
def bridge():
    return MagicMock()


def _make_server(registry, bridge, *, auth_enabled: bool = False, auth_token: str | None = None):
    return WebSocketServer(
        registry,
        bridge,
        host="127.0.0.1",
        port=0,
        enable_heartbeat=False,
        auth_enabled=auth_enabled,
        auth_token=auth_token,
    )


def _remote_request(*, query=None, headers=None, cookies=None):
    """远端（非 loopback）请求替身：remote 为公网 IP。"""
    request = MagicMock(spec=web.Request)
    request.query = query or {}
    request.headers = headers or {}
    request.cookies = cookies or {}
    request.remote = "203.0.113.7"  # TEST-NET-3，非 localhost
    transport = MagicMock()
    transport.get_extra_info.return_value = ("203.0.113.7", 54321)
    request.transport = transport
    return request


# ── 1. 认证 × 审批 ──


@pytest.mark.asyncio
async def test_combo_auth_approval_remote_without_token_denied_before_r4(registry, bridge):
    """远端无 token → 认证闸门拒绝；R4 审批路径在认证之后，根本不可达。"""
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    request = _remote_request()  # 无 token / 无 session

    assert server._is_loopback(request) is False, "远端不应被本机信任分支放行"
    assert server._verify_auth(request) is False, "远端无凭证必须被认证闸门拒绝"

    # 认证拒绝发生在 dispatch 之前：R4 删除的审批队列/执行均不应被触碰。
    # （R4 闸门本体由 test_p0_6_delete_r4_gating.py 覆盖；此处验证两层顺序）
    registry.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_combo_auth_approval_valid_token_reaches_r4_pending(registry, bridge):
    """持有效 token 的远端可过认证，但 R4 删除仍必须进审批（认证≠放行破坏性操作）。"""
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    request = _remote_request(headers={"Authorization": "Bearer secret"})
    assert server._verify_auth(request) is True

    # R4 闸门本体：未审批 dispatch → requires_approval + pending，不执行
    reg = MethodRegistry()
    executed: list[str] = []

    async def spy_delete(note_id: str = "", **kwargs: object) -> dict:
        executed.append(note_id)
        return {"deleted": note_id}

    reg.register("notes.delete", spy_delete, risk=RISK_R4)
    fake_sandbox = MagicMock()
    fake_sandbox.add.return_value = "pid-1"
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake_sandbox):
        result = await reg.dispatch("notes.delete", {"note_id": "X"})

    assert result.get("requires_approval") is True
    assert executed == [], "认证通过 ≠ R4 删除直接执行（必须审批）"


# ── 2. 认证 × settings ──


@pytest.mark.asyncio
async def test_combo_auth_settings_remote_without_token_denied(registry, bridge):
    """远端无 token → 认证拒绝，settings.set 不可达（设置域同样受认证保护）。"""
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    assert server._verify_auth(_remote_request()) is False


@pytest.mark.asyncio
async def test_combo_auth_settings_masking_after_auth(registry, bridge):
    """持有效 token → settings.set 可达，但敏感键写读均脱敏（认证+脱敏双层）。"""
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    assert server._verify_auth(_remote_request(headers={"Authorization": "Bearer secret"})) is True

    from backend.handlers._system_settings import register_system_settings

    reg = MethodRegistry()
    ctx = SimpleNamespace(
        settings_repository=MagicMock(),
        recreate_ai_client=MagicMock(),
        agent_gateway=MagicMock(),
        local_data_store=MagicMock(),
    )
    ctx.settings_repository.get_setting = lambda k, d=None: "sk-secret-value-123"
    register_system_settings(reg, ctx, 18765, MagicMock())
    handler = reg._handlers["settings.get"]
    result = await handler(key="openai_api_key")
    assert isinstance(result, dict)
    assert result.get("masked") is True, "敏感键必须标记 masked"
    assert result.get("value") != "sk-secret-value-123"
    assert "***" in result.get("value", ""), "敏感键值不得明文返回"


# ── 3. 认证 × cron ──


@pytest.mark.asyncio
async def test_combo_auth_cron_remote_without_token_denied(registry, bridge):
    """远端无 token → 认证拒绝，cron.add 不可达。"""
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    assert server._verify_auth(_remote_request()) is False


@pytest.mark.asyncio
async def test_combo_auth_cron_domain_risk_grading_holds(registry, bridge):
    """持有效 token → cron 域可达，但域内风险分级不放松：
    cron.add 非 R4（可执行），cron.remove 仍为 R4（破坏性，须审批）。"""
    server = _make_server(registry, bridge, auth_enabled=True, auth_token="secret")
    assert server._verify_auth(_remote_request(headers={"Authorization": "Bearer secret"})) is True

    reg = MethodRegistry()
    ctx = SimpleNamespace(local_data_store=SimpleNamespace(base_dir="/tmp/siyi-r2"), settings_repository=MagicMock())
    await register_all(reg, ctx, ws_port=18765)

    assert reg.has_method("cron.add"), "cron.add 必须已注册"
    assert reg.is_r4("cron.add") is False, "cron.add 非破坏性，不应 R4"
    assert reg.has_method("cron.remove"), "cron.remove 必须已注册"
    assert reg.is_r4("cron.remove") is True, "cron.remove 是破坏性操作，必须 R4（P0-6 收口）"


# ── 4. 水位 × 认证 ──


@pytest.mark.asyncio
async def test_combo_watermark_auth_failure_stops_sync_loop() -> None:
    """云同步认证失败 → sync.failed fatal:True + stop_sync_loop（水位不推进）。"""
    client = CloudSyncClient(
        worker_url="https://worker.example.test",
        sync_token="user-token",
        device_id="desktop-test",
        register_secret="reg-secret",
        note_repo=MagicMock(),
        task_repo=MagicMock(),
        settings_repo=MagicMock(),
        initial_cursor=42,
    )
    assert client.is_configured() is True

    events: list[tuple[str, dict]] = []

    async def _push_fails_auth() -> bool:
        raise CloudSyncAuthError("upload rejected: invalid token")

    with patch.object(client, "push_local_data", side_effect=_push_fails_auth), \
         patch.object(client, "_emit_sync_event", side_effect=lambda et, p: events.append((et, p))), \
         patch.object(client, "stop_sync_loop") as stop_mock:
        ok = await client.sync_once()

    assert ok is False, "认证失败 sync_once 必须返回 False"
    assert len(events) == 1, "必须发出 sync.failed 事件"
    event_type, payload = events[0]
    assert event_type == "sync.failed"
    assert payload.get("fatal") is True, "认证失败必须标 fatal（通知管道才会转发）"
    assert "error" in payload and "invalid token" in payload["error"]
    stop_mock.assert_called_once(), "认证失败必须停止同步循环（防 Worker 封禁）"
    assert client._cursor == 42, "认证失败不得推进水位"
