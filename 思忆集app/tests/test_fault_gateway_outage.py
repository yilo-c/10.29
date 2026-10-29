"""L4 异常/故障注入 —— gateway 停机降级（S1 核心）。

增量定位：现有 `test_kernel_status.py` 用 mock gateway 覆盖三种路由状态；
本文件聚焦**真实适配器故障语义**与**停机/恢复翻转**：

1. 组件级（确定性，真实适配器）：
   - DshGateway/NoneGateway 停机语义：cli 缺失 → health().available=False、
     submit() 返回 status="unavailable"（不抛异常）；
   - 停机（gateway 缺失/none）→ degraded + butler(fallback)；
   - 恢复（注入真实 DshGateway）→ 翻回 gateway；
   - 存在性判定只看 name：gateway 在场但 health 失败仍 degraded=False（降级只由缺失触发）；
   - chat_route=legacy 强制 butler（显式配置，非降级）。
2. 集成级（契约观察，只读）：`kernel.status` RPC 字段契约稳定。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from backend.handlers._kernel import build_kernel_status
from services.agent_gateway.adapters import DshGateway, NoneGateway
from services.agent_gateway.routing import get_gateway


class _FakeRepo:
    """最小 settings 仓库替身（仅 get_setting）。"""

    def __init__(self, values: dict | None = None) -> None:
        self._v = values or {}

    def get_setting(self, key: str, default=None):
        return self._v.get(key, default)


def _ctx(agent_gateway=None, settings: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        agent_gateway=agent_gateway,
        settings_repository=_FakeRepo(settings),
    )


# ── 组件级：真实适配器停机语义 ──────────────────────────────────────────────


class TestDshAdapterOutageSemantics:
    def test_health_available_false_when_cli_missing(self, monkeypatch) -> None:
        """DSH CLI 缺失（停机）→ health().available=False，不抛异常。"""
        monkeypatch.setattr("services.agent_gateway.adapters.shutil.which", lambda _n: None)
        gw = DshGateway()
        h = gw.health()
        assert h["available"] is False
        assert h["kernel"] == "dsh"
        assert h["exe"] is None

    def test_submit_unavailable_when_cli_missing(self, monkeypatch) -> None:
        """停机时 submit 返回 status='unavailable'，而不是抛异常。"""
        monkeypatch.setattr("services.agent_gateway.adapters.shutil.which", lambda _n: None)
        gw = DshGateway()
        out = gw.submit("hello")
        assert out.status == "unavailable"
        assert out.raw["error_kind"] == "unavailable"
        assert out.error  # AgentRun.error 承载可读错误消息
        assert "CLI" in out.error

    def test_none_gateway_submit_unavailable(self) -> None:
        """agent_kernel.type=none 等价停机：submit 恒 unavailable。"""
        out = NoneGateway().submit("hello")
        assert out.status == "unavailable"
        assert out.raw["error_kind"] == "unavailable"


# ── 组件级：停机 / 恢复翻转（真实适配器） ──────────────────────────────────


class TestGatewayOutageDegradation:
    def test_outage_no_gateway_degrades_to_butler(self) -> None:
        """停机（gateway 缺失）→ degraded + butler(fallback)（显式灰度 gateway）。"""
        st = build_kernel_status(_ctx(agent_gateway=None, settings={"chat_route": "gateway"}))
        assert st["degraded"] is True
        assert st["effective_engine"] == "butler(fallback)"
        assert st["gateway_present"] is False
        assert st["route"] == "gateway"  # 路由意图仍是 gateway
        assert st["gateway_name"] is None
        assert "降级" in st["message"]
        assert st["details"]["degraded"] is True

    def test_none_gateway_degrades(self) -> None:
        """NoneGateway(name='none') 等价于停机。"""
        st = build_kernel_status(_ctx(agent_gateway=NoneGateway(), settings={"chat_route": "gateway"}))
        assert st["degraded"] is True
        assert st["gateway_present"] is False
        assert st["effective_engine"] == "butler(fallback)"

    def test_recovery_flips_back_to_gateway(self) -> None:
        """恢复（注入真实 DshGateway）→ 翻回 gateway，非降级。"""
        gw = DshGateway(profile="headless")
        st = build_kernel_status(_ctx(agent_gateway=gw, settings={"chat_route": "gateway"}))
        assert st["degraded"] is False
        assert st["effective_engine"] == "gateway"
        assert st["gateway_present"] is True
        assert st["gateway_name"] == "dsh"
        # details 附带真实 health 快照（存在性判定只依赖 name，不依赖健康）
        assert st["details"]["gateway_health"]["kernel"] == "dsh"

    def test_present_but_unhealthy_is_not_degraded(self, monkeypatch) -> None:
        """在场但 health 失败（如 CLI 缺失）≠ 降级：降级只由网关缺失触发。"""
        monkeypatch.setattr("services.agent_gateway.adapters.shutil.which", lambda _n: None)
        gw = DshGateway()
        st = build_kernel_status(_ctx(agent_gateway=gw, settings={"chat_route": "gateway"}))
        assert st["gateway_present"] is True
        assert st["degraded"] is False
        assert st["effective_engine"] == "gateway"
        assert st["details"]["gateway_health"]["available"] is False  # 健康快照如实反映

    def test_legacy_route_pins_butler_even_with_gateway(self) -> None:
        """chat_route=legacy 时强制 butler 路径（非降级，是显式配置）。"""
        gw = DshGateway(profile="headless")
        st = build_kernel_status(_ctx(agent_gateway=gw, settings={"chat_route": "legacy"}))
        assert st["route"] == "legacy"
        assert st["effective_engine"] == "butler"
        assert st["degraded"] is False

    def test_get_gateway_none_semantics(self) -> None:
        """get_gateway 把 name='none' 的网关归一为 None（对外等价停机）。"""
        assert get_gateway(_ctx(agent_gateway=NoneGateway())) is None
        assert get_gateway(_ctx(agent_gateway=None)) is None
        gw = DshGateway(profile="headless")
        assert get_gateway(_ctx(agent_gateway=gw)) is gw


# ── 集成级：kernel.status 契约观察（只读） ─────────────────────────────────


async def test_kernel_status_contract_via_rpc(rpc) -> None:
    """真实后端 kernel.status 字段契约稳定、值合法。"""
    result = (await rpc("kernel.status"))["result"]
    assert isinstance(result, dict)
    for field in (
        "route",
        "effective_engine",
        "degraded",
        "gateway_present",
        "gateway_name",
        "kernel_type",
        "message",
        "details",
    ):
        assert field in result, f"kernel.status 缺字段: {field}"
    assert result["route"] in ("legacy", "gateway")
    assert result["effective_engine"] in ("gateway", "butler", "butler(fallback)")
    assert isinstance(result["degraded"], bool)
    assert isinstance(result["gateway_present"], bool)
    assert isinstance(result["gateway_name"], (str, type(None)))
    assert isinstance(result["kernel_type"], str)
    assert result["details"]["route"] == result["route"]
    assert result["details"]["degraded"] == result["degraded"]


async def test_kernel_status_stable_over_time(rpc) -> None:
    """短窗口内两次读取状态一致（无抖动）。"""
    a = (await rpc("kernel.status"))["result"]
    time.sleep(0.3)
    b = (await rpc("kernel.status"))["result"]
    assert a["degraded"] == b["degraded"]
    assert a["effective_engine"] == b["effective_engine"]
