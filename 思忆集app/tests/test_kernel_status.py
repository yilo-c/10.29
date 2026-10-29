"""内核可观测性测试：kernel.status RPC + routing 共享层 + system.health 内核子系统。

验证 Flip 回翻后（chat_route 缺省 legacy）三种生效态：
- gateway 生效（显式 route=gateway 且网关在线）
- 降级（显式 route=gateway 但网关不可用 → 落 butler，degraded=True）
- legacy（缺省或 chat_route=legacy → 经典 butler，不降级）
"""

from __future__ import annotations

import types

from backend.handlers._kernel import build_kernel_status
from services.agent_gateway.routing import (
    get_gateway,
    kernel_type,
    resolve_chat_route,
)
from services.system_health import SEVERITY_DEGRADED, SEVERITY_HEALTHY, SEVERITY_UNKNOWN, _collect_kernel


def _kernel_ctx(agent_gateway=None, chat_route=None, gateway_type=None):
    settings = None
    if chat_route is not None:
        settings = types.SimpleNamespace(get_setting=lambda k, d=None: chat_route if k == "chat_route" else d)
    app_config = None
    if gateway_type is not None:
        app_config = types.SimpleNamespace(get=lambda k, d=None: gateway_type if k == "gateway_type" else d)
    return types.SimpleNamespace(
        agent_gateway=agent_gateway,
        settings_repository=settings,
        app_config=app_config,
    )


def _fake_gw(name="dsh", gw_type="dsh", health_ok=True):
    return types.SimpleNamespace(
        name=name,
        type=gw_type,
        health_check=(lambda: (health_ok, None)) if health_ok else (lambda: (False, "conn refused")),
    )


# ── routing 共享层 ─────────────────────────────────────────────
def test_resolve_chat_route_default_legacy():
    # Flip 回翻（2026-09）：无 settings_repository / 无该键时缺省 legacy（壳内管家）
    ctx = _kernel_ctx(agent_gateway=_fake_gw())
    assert resolve_chat_route(ctx) == "legacy"


def test_resolve_chat_route_gateway_only_if_explicit():
    # 灰度开关语义：仅显式 gateway 才走网关
    ctx = _kernel_ctx(agent_gateway=_fake_gw(), chat_route="gateway")
    assert resolve_chat_route(ctx) == "gateway"


def test_resolve_chat_route_respects_setting():
    ctx = _kernel_ctx(agent_gateway=_fake_gw(), chat_route="legacy")
    assert resolve_chat_route(ctx) == "legacy"


def test_get_gateway_returns_injected():
    gw = _fake_gw()
    ctx = _kernel_ctx(agent_gateway=gw)
    assert get_gateway(ctx) is gw


def _settings_ctx(**kv):
    return types.SimpleNamespace(settings_repository=types.SimpleNamespace(get_setting=lambda k, d=None: kv.get(k, d)))


def test_kernel_type_from_settings():
    # kernel_type 读 settings 的 siyi_kernel_type / siyi_kernel（非注入网关 name）
    assert kernel_type(_settings_ctx(siyi_kernel_type="dsh")) == "dsh"
    assert kernel_type(_settings_ctx(siyi_kernel="claude")) == "claude"


def test_kernel_type_no_settings():
    # 内核路由 Flip 决策后：已注入 gateway 的 name 是单一事实来源，
    # 无 settings 时也返回注入网关的 name（dsh），不再要求 settings 存在。
    assert kernel_type(_kernel_ctx(agent_gateway=_fake_gw())) == "dsh"
    # 兜底路径不变：无注入网关且无 settings → none
    assert kernel_type(_kernel_ctx()) == "none"


# ── kernel.status RPC ─────────────────────────────────────────
def test_kernel_status_gateway_active():
    ctx = _kernel_ctx(agent_gateway=_fake_gw(), chat_route="gateway")
    s = build_kernel_status(ctx)
    assert s["route"] == "gateway"
    assert s["effective_engine"] == "gateway"
    assert s["degraded"] is False
    assert s["gateway_present"] is True


def test_kernel_status_default_ctx_is_butler():
    # 缺省（无 chat_route 设置）→ legacy 管家路径，即使网关已注入
    ctx = _kernel_ctx(agent_gateway=_fake_gw())
    s = build_kernel_status(ctx)
    assert s["route"] == "legacy"
    assert s["effective_engine"] == "butler"
    assert s["degraded"] is False


def test_kernel_status_gateway_fallback():
    # route=gateway 但网关缺失 → 降级到 butler
    ctx = _kernel_ctx(agent_gateway=None, chat_route="gateway")
    s = build_kernel_status(ctx)
    assert s["route"] == "gateway"
    assert s["effective_engine"] == "butler(fallback)"
    assert s["degraded"] is True


def test_kernel_status_legacy():
    ctx = _kernel_ctx(agent_gateway=_fake_gw(), chat_route="legacy")
    s = build_kernel_status(ctx)
    assert s["route"] == "legacy"
    assert s["effective_engine"] == "butler"
    assert s["degraded"] is False


# ── system.health 内核子系统 ──────────────────────────────────
def test_collect_kernel_disabled_unknown():
    ctx = _kernel_ctx(agent_gateway=None)
    snap = _collect_kernel(ctx)
    assert snap["severity"] == SEVERITY_UNKNOWN
    assert snap["available"] is False


def test_collect_kernel_healthy():
    ctx = _kernel_ctx(agent_gateway=_fake_gw(health_ok=True))
    snap = _collect_kernel(ctx)
    assert snap["severity"] == SEVERITY_HEALTHY
    assert snap["available"] is True


def test_collect_kernel_degraded():
    ctx = _kernel_ctx(agent_gateway=_fake_gw(health_ok=False))
    snap = _collect_kernel(ctx)
    assert snap["severity"] == SEVERITY_DEGRADED
    assert snap["available"] is True
