"""AutoGateway 单元测试：内核探测 + 失败自动切换。

用 monkeypatch 替换候选适配器的 health/submit，验证：
- 探测：仅可用内核入选，第一个可用者为 active
- 失败切换：连续失败达阈值后切换到下一个候选
- 切换后重试：新内核成功则返回新结果
- 无可用候选：行为同 NoneGateway（unavailable）
- resume/cancel 透传当前 active
"""
import pytest

from services.agent_gateway.auto import AutoGateway, _build_candidate
from services.agent_gateway.factory import build_gateway, parse_agent_kernel_value
from services.agent_gateway.protocol import AgentRun, AgentRunEvent, BaseGateway


class FakeGateway(BaseGateway):
    """可编程假内核：health/submit 行为由测试注入。"""

    def __init__(self, name: str, available: bool = True, result: str = "ok", fail_times: int = 0):
        self.name = name
        self._available = available
        self._result = result
        self._fail_times = fail_times  # 前 N 次 submit 失败，之后成功
        self.calls = 0

    def health(self) -> dict:
        return {"kernel": self.name, "available": self._available}

    def submit(self, prompt: str, timeout_s: int | None = None, profile: str | None = None) -> AgentRun:
        self.calls += 1
        if self.calls <= self._fail_times:
            return AgentRun(status="failed", error="boom", raw={"error_kind": "internal"})
        return AgentRun(status="succeeded", result=self._result)


def _patch_pool(monkeypatch, auto: AutoGateway, fakes: dict[str, FakeGateway]) -> None:
    """把 AutoGateway 内部池替换为假内核。"""
    monkeypatch.setattr(auto, "_pool", fakes)
    monkeypatch.setattr(auto, "_order", list(fakes.keys()))
    auto._available = [k for k, gw in fakes.items() if gw._available]
    auto._active = auto._available[0] if auto._available else None


# ── 探测 ──────────────────────────────────────────────────
def test_probe_selects_first_available(monkeypatch):
    fakes = {
        "dsh": FakeGateway("dsh", available=False),
        "claude_code": FakeGateway("claude_code", available=True),
        "codex": FakeGateway("codex", available=True),
    }
    auto = AutoGateway(candidates=["dsh", "claude_code", "codex"])
    _patch_pool(monkeypatch, auto, fakes)
    # 重新探测（_patch_pool 后 active 已按 available 选定）
    auto._probe()
    assert auto._active == "claude_code"
    assert auto._available == ["claude_code", "codex"]


def test_probe_all_unavailable_becomes_none(monkeypatch):
    fakes = {
        "dsh": FakeGateway("dsh", available=False),
        "codex": FakeGateway("codex", available=False),
    }
    auto = AutoGateway(candidates=["dsh", "codex"])
    _patch_pool(monkeypatch, auto, fakes)
    auto._probe()
    assert auto._active is None
    run = auto.submit("hello")
    assert run.status == "unavailable"


# ── 失败切换 ──────────────────────────────────────────────
def test_failover_switches_after_threshold(monkeypatch):
    fakes = {
        "dsh": FakeGateway("dsh", available=True, fail_times=99),  # 永远失败
        "codex": FakeGateway("codex", available=True, result="codex-ok"),
    }
    auto = AutoGateway(candidates=["dsh", "codex"], max_failures=2)
    _patch_pool(monkeypatch, auto, fakes)

    # 第 1 次失败：计数 1，未达阈值，仍 active=dsh
    run1 = auto.submit("t")
    assert run1.status == "failed"
    assert auto._active == "dsh"
    assert auto._consecutive_failures == 1

    # 第 2 次失败：达阈值 → 切换到 codex → 重试成功
    run2 = auto.submit("t")
    assert run2.status == "succeeded"
    assert run2.result == "codex-ok"
    assert auto._active == "codex"
    assert auto._switch_count == 1
    assert auto._consecutive_failures == 0


def test_success_resets_failure_counter(monkeypatch):
    fakes = {
        "dsh": FakeGateway("dsh", available=True, fail_times=1, result="dsh-ok"),
    }
    auto = AutoGateway(candidates=["dsh"], max_failures=2)
    _patch_pool(monkeypatch, auto, fakes)

    auto.submit("t")  # 失败 1 次
    assert auto._consecutive_failures == 1
    run = auto.submit("t")  # 成功
    assert run.status == "succeeded"
    assert auto._consecutive_failures == 0
    assert auto._active == "dsh"  # 未切换


def test_health_reports_active_and_switch_history(monkeypatch):
    fakes = {
        "dsh": FakeGateway("dsh", available=True, fail_times=99),
        "codex": FakeGateway("codex", available=True, result="ok"),
    }
    auto = AutoGateway(candidates=["dsh", "codex"], max_failures=1)
    _patch_pool(monkeypatch, auto, fakes)
    auto.submit("t")  # 触发切换

    h = auto.health()
    assert h["active"] == "codex"
    assert h["available_candidates"] == ["dsh", "codex"]
    assert h["switch_count"] == 1
    assert len(h["switch_history"]) == 1
    assert h["switch_history"][0]["from"] == "dsh"
    assert h["switch_history"][0]["to"] == "codex"


# ── 坏 cli_path 探测（auto 正确性关键路径）──────────────
def test_dsh_health_false_for_missing_cli_path(monkeypatch, tmp_path):
    """cli_path 指向不存在文件时，DshGateway.health 必须报不可用。"""
    from services.agent_gateway.adapters import DshGateway

    missing = str(tmp_path / "no-such-dsh.exe")
    gw = DshGateway(cli_path=missing)
    h = gw.health()
    assert h["available"] is False
    assert h["exe"] == missing
    run = gw.submit("hi")
    assert run.status == "unavailable"


def test_auto_probe_skips_missing_cli_path(monkeypatch, tmp_path):
    """auto 探测：dsh cli_path 失效时跳到下一个可用候选。"""
    from services.agent_gateway.adapters import DshGateway, CodexGateway

    missing = str(tmp_path / "no-such-dsh.exe")
    fake_codex = FakeGateway("codex", available=True, result="codex-ok")
    auto = AutoGateway(candidates=["dsh", "codex"], cfg={"dsh": {"cli_path": missing}})
    # 替换池：dsh 用真实 DshGateway（坏路径），codex 用假内核
    monkeypatch.setattr(auto, "_pool", {
        "dsh": DshGateway(cli_path=missing),
        "codex": fake_codex,
    })
    auto._probe()
    assert auto._active == "codex"
    assert auto._available == ["codex"]


# ── factory 集成 ──────────────────────────────────────────
def test_factory_auto_builds_autogateway(monkeypatch):
    cfg = parse_agent_kernel_value({"type": "auto", "auto": {"candidates": ["dsh", "codex"]}})
    gw = build_gateway(cfg)
    assert gw.name == "auto"
    h = gw.health()
    assert h["kernel"] == "auto"
    assert "candidate_order" in h


def test_factory_dsh_still_builds_dsh():
    gw = build_gateway({"type": "dsh"})
    assert gw.name == "dsh"


def test_factory_unknown_type_falls_back_none():
    gw = build_gateway({"type": "martian"})
    assert gw.name == "none"


# ── _build_candidate ──────────────────────────────────────
def test_build_candidate_unknown_returns_none_gateway():
    gw = _build_candidate("martian", {})
    assert gw.name == "none"


def test_build_candidate_dsh():
    gw = _build_candidate("dsh", {"profile": "headless"})
    assert gw.name == "dsh"
    assert gw.profile == "headless"
