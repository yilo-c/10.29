"""R1-R4 风险分级与审批闸门补漏测试（审批策略 · 任务 #3）。

既有覆盖：test_r4_approval_resolution（决议闭环）/ test_p0_6_delete_r4_gating
（delete 收口）/ test_sandbox_permission_boundary + test_sandbox_timeout_batch
（SandboxManager 队列与超时策略）。本文件收口三处此前 0 覆盖的策略缺口：

  G1 settings.set 动态判级——backend/handlers/__init__.py 注释引用的本文件名
     一直缺位（悬空引用）：敏感键（api_key/token/secret/password...）→ R4 →
     dispatch 返回 pending 不执行；普通键 → R3 直接执行。resolver 注入是
     try/except 静默回退的，注入失败只有这里能暴露。
  G2 dispatch_stream 流式 R4 闸门——R4 方法经流式通道只 yield 单条 pending，
     不进入 handler；skip_approval=True 重放时正常流式执行。
  G3 risk resolver 健壮性——resolver 异常/非法返回值回退静态分级（保守缺省
     R3，不 crash 不 fail-open 到放行）；set_risk 拒绝非法分级。

隔离：G1/G2 用真实 register_all + MagicMock context（自管测试方法）；
G3 用裸 MethodRegistry 纯内存，不依赖真实后端/文件系统。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.handlers import register_all
from backend.method_registry import RISK_R4, MethodRegistry
from _fake_settings import FakeSettingsRepository


class _CollectingContext:
    """与 test_r4_approval_resolution 同构的最小 context。

    settings_repository 预配置：validate_setting → (True, "")（settings_set
    内部按 `ok, err = await to_thread(repo.validate_setting, ...)` 解包，
    裸 MagicMock 解包会 ValueError）、get_setting → []（llm_ 键同步分支短路）。
    """

    def __init__(self) -> None:
        self.local_data_store = SimpleNamespace(base_dir="/tmp/siyi-risk-test")
        repo = MagicMock()
        repo.validate_setting.return_value = (True, "")
        repo.set_setting.return_value = None
        # 2026-09-18：`get_setting` 不再用 blanket `[]` 桩（类型坍缩，见
        # tests/_fake_settings.py 动机说明），改为委托给回落 SETTINGS_DEFAULTS
        # 真源的假仓储 —— 其余方法（validate_setting 等）仍走 MagicMock。
        _typed = FakeSettingsRepository()
        repo.get_setting.side_effect = _typed.get_setting
        self.settings_repository = repo


@pytest.fixture
async def full_reg() -> MethodRegistry:
    reg = MethodRegistry()
    ctx = _CollectingContext()
    await register_all(reg, ctx, ws_port=18766)
    reg._ctx = ctx  # type: ignore[attr-defined]
    # 清空 register_all 期间各 handler 的 settings 访问历史、保留预配置返回值，
    # 测试从零计数断言。（PH-A1, 2026-09-17：register_transfer 已不再生成/持久化
    # transfer token，注册期副作用进一步减少。）
    ctx.settings_repository.reset_mock()
    return reg


async def _send(chunk, chunk_type: str = "chunk") -> None:
    return None


# ── G3：risk resolver 语义（裸注册中心，纯内存） ──────────────────────


class TestRiskResolverSemantics:
    def test_resolver_wins_over_static(self) -> None:
        """动态 resolver 优先于静态分级（settings.set 静态 R3 靠它升 R4）。"""
        reg = MethodRegistry()
        reg.set_risk("m", "R3")
        reg.set_risk_resolver("m", lambda method, params: "R4")
        assert reg.is_r4("m", {}) is True

    def test_resolver_exception_falls_back_to_static(self) -> None:
        reg = MethodRegistry()
        reg.set_risk("m", "R3")

        def _boom(method: str, params: dict) -> str:
            raise RuntimeError("resolver boom")

        reg.set_risk_resolver("m", _boom)
        assert reg.resolve_risk("m", {"k": 1}) == "R3"
        assert reg.is_r4("m") is False

    def test_resolver_invalid_return_falls_back_to_static(self) -> None:
        """resolver 返回非法分级（R9）时告警并回退静态，不采用脏值。"""
        reg = MethodRegistry()
        reg.set_risk("m", "R3")
        reg.set_risk_resolver("m", lambda method, params: "R9")
        assert reg.resolve_risk("m", {}) == "R3"

    def test_set_risk_invalid_level_raises(self) -> None:
        reg = MethodRegistry()
        with pytest.raises(ValueError):
            reg.set_risk("m", "R9")

    def test_unregistered_method_defaults_to_r3(self) -> None:
        """未标注方法保守缺省 R3（中等），不静默放行为 R1。"""
        reg = MethodRegistry()
        assert reg.get_risk("never.registered") == "R3"
        assert reg.is_r4("never.registered") is False


# ── G1：settings.set 动态判级（真实 register_all 链路） ───────────────


class TestSettingsSetDynamicRisk:
    @pytest.mark.asyncio
    async def test_sensitive_key_pends_without_executing(self, full_reg: MethodRegistry) -> None:
        res = await full_reg.dispatch("settings.set", {"key": "model.api_key", "value": "sk-test-123"})
        assert res.get("pending") is True
        assert res.get("requires_approval") is True
        assert res.get("pending_id")
        full_reg._ctx.settings_repository.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_sensitive_key_word_variants_all_pend(self, full_reg: MethodRegistry) -> None:
        for key in ("llm_token", "db_password", "webhook_secret"):
            res = await full_reg.dispatch("settings.set", {"key": key, "value": "x"})
            assert res.get("pending") is True, f"敏感键 {key} 应入审批队列"

    @pytest.mark.asyncio
    async def test_normal_key_executes_directly(self, full_reg: MethodRegistry) -> None:
        res = await full_reg.dispatch("settings.set", {"key": "ui.theme", "value": "dark"})
        assert res is True, "普通键（R3）应直接执行，handler 返回 True"
        full_reg._ctx.settings_repository.set_setting.assert_called_once_with("ui.theme", "dark")


# ── G2：dispatch_stream 流式 R4 闸门 ─────────────────────────────────


class TestDispatchStreamR4Gate:
    @pytest.fixture
    def reg_with_r4_stream(self, full_reg: MethodRegistry) -> MethodRegistry:
        streamed: list[str] = []

        async def r4_stream(send_chunk, value: str = "", **_kwargs):
            streamed.append(value)
            yield f"chunk:{value}"

        full_reg.register("test.r4_stream", r4_stream, risk=RISK_R4)
        full_reg._streamed = streamed  # type: ignore[attr-defined]
        return full_reg

    @pytest.mark.asyncio
    async def test_stream_r4_yields_pending_only_without_executing(self, reg_with_r4_stream) -> None:
        chunks = [c async for c in reg_with_r4_stream.dispatch_stream("test.r4_stream", {"value": "V"}, _send)]
        assert len(chunks) == 1, "流式 R4 应只回单条 pending，不产生其他 chunk"
        assert chunks[0].get("pending") is True
        assert chunks[0].get("pending_id")
        assert reg_with_r4_stream._streamed == [], "流式 R4 未审批不应进入 handler"

    @pytest.mark.asyncio
    async def test_stream_r4_skip_approval_replays(self, reg_with_r4_stream) -> None:
        chunks = [c async for c in reg_with_r4_stream.dispatch_stream("test.r4_stream", {"value": "V"}, _send, skip_approval=True)]
        assert chunks == ["chunk:V"], "skip_approval 重放应正常流式执行"
        assert reg_with_r4_stream._streamed == ["V"]
