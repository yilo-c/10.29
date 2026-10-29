# -*- coding: utf-8 -*-
"""M2a 契约测试（kernel-contract.md §7）：CT-01 ~ CT-10 + ConfirmationStore + HealthMonitor。

用替身内核验证网关层契约不变式，不依赖真实 CLI（CT-10 用 monkeypatch 注入假 CLI 验证真实
subprocess 路径）。ConfirmationStore 用隔离临时目录。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.agent_gateway import (  # noqa: E402
    SETTINGS_KEY,
    AGENT_GATEWAY_ADAPTERS,
    AgentGateway,
    AgentRun,
    AgentRunEvent,
    BaseGateway,
    build_gateway,
    build_gateway_from_settings_value,
    parse_agent_kernel_value,
)
from services.agent_gateway.adapters import (  # noqa: E402
    ClaudeCodeGateway,
    CodexGateway,
    DshGateway,
    NoneGateway,
    _classify_error,
    _resolve_spawn_command,
    _write_model_patch_yaml,
)
from services.agent_gateway.health import GatewayHealthMonitor  # noqa: E402
from services.agent_gateway.routing import get_gateway, resolve_dsh_profile  # noqa: E402
from services.confirmation_store import ConfirmationStore  # noqa: E402


# ── 替身内核 ──────────────────────────────────────────────
class FakeKernelGateway(BaseGateway):
    name = "fake"

    def __init__(self, mode: str = "ok"):
        self.mode = mode

    def submit(self, prompt: str, timeout_s: int | None = None) -> AgentRun:
        if self.mode == "ok":
            return AgentRun(result="fake ok", status="succeeded", raw={"usage": {"tokens": 1}})
        if self.mode == "fail":
            return AgentRun(result=None, status="failed", error="boom", raw={"error_kind": "internal", "stderr": "boom"})
        if self.mode == "timeout":
            return AgentRun(result=None, status="failed", error="timed out", raw={"error_kind": "timeout"})
        if self.mode == "unavailable":
            return AgentRun(result=None, status="unavailable", error="no cli", raw={"error_kind": "unavailable"})
        return AgentRun(result=None, status="failed", error="?", raw={"error_kind": "internal"})

    def health(self) -> dict:
        if self.mode == "unavailable":
            return {"status": "degraded", "detail": {self.name: {"status": "unavailable"}}}
        return {"status": "healthy", "detail": {self.name: {"status": "healthy"}}}


class FakeStreamGateway(FakeKernelGateway):
    def submit_stream(self, prompt: str, timeout_s: int | None = None):
        yield AgentRunEvent(kind="status", payload={"phase": "started"})
        yield AgentRunEvent(kind="delta", payload={"text": "a"})
        yield AgentRunEvent(kind="delta", payload={"text": "b"})
        yield AgentRunEvent(kind="result", payload={"text": "ab", "usage": None})


def _validate_event_seq(events: list[AgentRunEvent]) -> bool:
    kinds = [e.kind for e in events]
    assert kinds[0] == "status" and events[0].payload.get("phase") == "started"
    assert kinds[-1] in ("result", "error")
    terminals = [i for i, k in enumerate(kinds) if k in ("result", "error")]
    assert len(terminals) == 1, "必须恰好一个终态事件"
    assert terminals[0] == len(kinds) - 1, "终态后不得再有事件"
    return True


# CT-01：build_config 含必需字段（v2 配置合并）
def test_ct01_build_config_required_fields():
    cfg = parse_agent_kernel_value(None)
    assert cfg.get("type") in ("dsh", "none", "claude_code", "codex")
    for key in ("dsh", "claude_code", "codex"):
        assert key in cfg
    gw = build_gateway_from_settings_value(None)
    assert isinstance(gw, AgentGateway)


# CT-02：unavailable 状态透传（含 error_kind）
def test_ct02_unavailable_passthrough():
    gw = FakeKernelGateway(mode="unavailable")
    run = gw.submit("x")
    assert run.status == "unavailable"
    assert run.raw.get("error_kind") == "unavailable"


# CT-03：注册表 key = agent_kernel
def test_ct03_registry_key():
    assert SETTINGS_KEY == "agent_kernel"
    assert set(__import__("services.agent_gateway", fromlist=["AGENT_GATEWAY_ADAPTERS"]).AGENT_GATEWAY_ADAPTERS) == {
        "none",
        "dsh",
        "claude_code",
        "codex",
    }


# CT-04：error_kind 归一化
@pytest.mark.parametrize(
    "stderr,rc,expected",
    [
        ("401 unauthorized", 1, "auth"),
        ("invalid api key", 1, "auth"),
        ("429 too many requests", 1, "quota"),
        ("rate limit exceeded", 1, "quota"),
        ("operation timed out", 1, "timeout"),
        ("something crashed", 1, "internal"),
        ("ok", 0, "internal"),
    ],
)
def test_ct04_error_kind_normalization(stderr, rc, expected):
    assert _classify_error(stderr, rc) == expected


# CT-05：仅注册表声明的适配器
def test_ct05_registry_only_declared():
    from services.agent_gateway import AGENT_GATEWAY_ADAPTERS

    assert set(AGENT_GATEWAY_ADAPTERS) <= {"none", "dsh", "claude_code", "codex"}


# CT-06：Protocol 不变式
def test_ct06_protocol_invariants():
    for gw in (DshGateway(), ClaudeCodeGateway(), CodexGateway(), NoneGateway(), FakeKernelGateway()):
        assert isinstance(gw, AgentGateway)


# CT-07：submit_stream 状态机
def test_ct07_stream_state_machine():
    assert _validate_event_seq(list(FakeKernelGateway(mode="ok").submit_stream("x")))
    assert _validate_event_seq(list(FakeKernelGateway(mode="fail").submit_stream("x")))
    assert _validate_event_seq(list(FakeStreamGateway(mode="ok").submit_stream("x")))
    bad = [
        AgentRunEvent(kind="status", payload={"phase": "started"}),
        AgentRunEvent(kind="result", payload={"text": "x"}),
        AgentRunEvent(kind="delta", payload={"text": "y"}),
    ]
    with pytest.raises(AssertionError):
        _validate_event_seq(bad)


# CT-08：AgentRun 字段完整
def test_ct08_agentrun_fields():
    run = FakeKernelGateway(mode="ok").submit("x")
    for attr in ("run_id", "status", "result", "error", "raw"):
        assert hasattr(run, attr)
    assert run.status == "succeeded" and run.result == "fake ok"


# CT-09：同步 submit 返回 AgentRun
def test_ct09_sync_submit_returns_agentrun():
    gw = build_gateway({"type": "none"})
    run = gw.submit("hi")
    assert isinstance(run, AgentRun)
    assert run.status == "unavailable"  # none 内核恒 unavailable


# CT-10：真实 CLI 注入路径（monkeypatch 假 dsh）
def test_ct10_real_cli_injection(monkeypatch, tmp_path):
    fake = tmp_path / "fake_dsh.cmd"
    fake.write_text('@echo {"result":"fake","status":"succeeded"}\n', encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda name: str(fake) if name == "dsh" else None)
    gw = DshGateway(profile="headless", timeout_s=10)
    run = gw.submit("hi")
    assert run.status == "succeeded"
    assert "fake" in (run.result or "")


# ── npm .cmd 垫片多行 argv 截断（2026-09-11 "内核说没有任务"事故）──
# 根因：npm 全局 CLI 是 .cmd 批处理垫片，subprocess 直接 spawn 之会经 cmd.exe
# 重建命令行，含 \n 的参数在第一个换行处被截断——_build_kernel_prompt 拼出的
# 多行消息只剩首行「以下是近期对话记录（供理解上下文）：」，内核收不到任务正文。
# 修复：_resolve_spawn_command 解析垫片内 node JS 入口，绕过 cmd.exe 直调。

_PROBE_JS = Path(__file__).resolve().parent / "fixtures" / "argv_echo_probe.js"


def _make_npm_shim(tmp_path: Path, bin_js: Path) -> Path:
    """生成一个与 npm 真实格式同构的 .cmd 垫片（JS 入口拷入 tmp 保证相对路径）。"""
    shim = tmp_path / "fake-cli.cmd"
    target = tmp_path / bin_js.name
    if not target.exists():
        shutil.copyfile(bin_js, target)
    rel = str(target.relative_to(tmp_path)).replace("/", "\\")
    shim.write_text(
        "@ECHO off\r\n"
        "GOTO start\r\n"
        ":find_dp0\r\n"
        "SET dp0=%~dp0\r\n"
        "EXIT /b\r\n"
        ":start\r\n"
        "SETLOCAL\r\n"
        "CALL :find_dp0\r\n"
        'IF EXIST "%dp0%\\node.exe" (\r\n'
        '  SET "_prog=%dp0%\\node.exe"\r\n'
        ") ELSE (\r\n"
        '  SET "_prog=node"\r\n'
        "  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n"
        ")\r\n"
        'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%" '
        f'"%dp0%\\{rel}" %*\r\n',
        encoding="utf-8",
    )
    return shim


def test_spawn_command_non_cmd_passthrough():
    assert _resolve_spawn_command(r"C:\tools\codex.exe") == [r"C:\tools\codex.exe"]
    assert _resolve_spawn_command("plain") == ["plain"]


def test_spawn_command_unparseable_shim_passthrough(tmp_path):
    """无法解析出 node JS 入口的 .cmd 原样返回（不猜测、保持行为兼容）。"""
    fake = tmp_path / "weird.cmd"
    fake.write_text("@echo hello\r\n", encoding="utf-8")
    assert _resolve_spawn_command(str(fake)) == [str(fake)]


@pytest.mark.skipif(os.name != "nt", reason="npm .cmd 垫片行为仅存在于 Windows")
def test_spawn_command_real_dsh_shim_resolves_to_node(tmp_path):
    """对生产真实 dsh 垫片（如在场）验证解析出 [node, bin.js] 二元组。"""
    real = shutil.which("dsh")
    if not real or not real.lower().endswith(".cmd"):
        pytest.skip("dsh CLI 不在 PATH 或非 .cmd 垫片")
    prefix = _resolve_spawn_command(real)
    assert len(prefix) == 2, f"应解析为 node+bin.js 直调：{prefix}"
    assert prefix[1].lower().endswith(".js")
    assert os.path.isfile(prefix[1])


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe 截断行为仅存在于 Windows")
def test_multiline_prompt_survives_shim_resolution(tmp_path):
    """端到端：多行参数经垫片解析后的直调路径完整到达；不经解析则被截断。"""
    node = shutil.which("node")
    if not node or not _PROBE_JS.is_file():
        pytest.skip("node 或探针 fixture 不可用")
    prompt = "第一行头\n第二行任务正文：查看桌面"
    # 对照组：直接 spawn .cmd 垫片 → cmd.exe 截断（复现生产 bug）
    shim = _make_npm_shim(tmp_path, _PROBE_JS)
    r_bad = subprocess.run([str(shim), prompt], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    got_bad = json.loads(r_bad.stdout.strip() or '"\n\n"')
    assert "\n" not in got_bad, f"对照组应复现截断，实收 {got_bad!r}"
    # 修复组：经 _resolve_spawn_command 解析后直调 → 完整保全
    prefix = _resolve_spawn_command(str(shim))
    assert len(prefix) == 2, f"垫片应解析为 node 直调：{prefix}"
    r_good = subprocess.run([*prefix, prompt], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    got_good = json.loads(r_good.stdout.strip() or '""')
    assert got_good == prompt, f"应完整到达，实收 {got_good!r}"


def test_model_patch_yaml_is_comment_free():
    """overlay 为无注释纯映射（最小暴露面约定；纠偏见 adapters 文档）。"""
    path = _write_model_patch_yaml("deepseek-v4-flash/deepseek-v4-flash")
    try:
        text = Path(path).read_text(encoding="utf-8")
    finally:
        os.remove(path)
    assert "#" not in text
    assert text.startswith("- id: ")
    assert "provider: deepseek-v4-flash" in text and "model: deepseek-v4-flash" in text


# ── GatewayHealthMonitor（契约 §5.3）─────────────────────
def test_health_monitor_degrade_on_timeouts():
    mon = GatewayHealthMonitor(FakeKernelGateway(mode="ok"), ttl_s=60, degraded_after=2)
    mon.record_outcome(AgentRun(status="failed", raw={"error_kind": "timeout"}))
    assert not mon.degraded
    mon.record_outcome(AgentRun(status="failed", raw={"error_kind": "timeout"}))
    assert mon.degraded
    mon.record_outcome(AgentRun(status="succeeded"))
    assert not mon.degraded


def test_health_monitor_ttl_cache():
    mon = GatewayHealthMonitor(FakeKernelGateway(mode="ok"), ttl_s=60, degraded_after=3)
    assert mon.check() == mon.check()


# ── ConfirmationStore（契约 §4.2）────────────────────────
def test_confirmation_create_resolve_get(tmp_path):
    cs = ConfirmationStore(data_dir=str(tmp_path))
    cid = cs.create("notes.delete", {"id": "x"}, source="mcp:kernel", title="删除笔记", detail="")
    assert isinstance(cid, str) and cid
    e = cs.get(cid)
    assert e["status"] == "pending"
    assert e["action_id"] == "notes.delete"
    assert e["source"] == "mcp:kernel"
    ent = cs.resolve(cid, True)
    assert ent is not None and ent["status"] == "approved"
    assert cs.resolve(cid, True) is None  # 已决议不可重复
    assert cs.get(cid)["status"] == "approved"


def test_confirmation_list_purge_ttl(tmp_path):
    cs = ConfirmationStore(data_dir=str(tmp_path))
    cid = cs.create("a", {}, source="butler", ttl_s=0.01)
    time.sleep(0.05)
    # 过期 pending 直接 purge 清理（不经过 get 标记）
    assert cs.purge_expired() >= 1
    assert cs.get(cid) is None


# ── F 项：L1 per-request 内核选择（2026-08-20）──────────────────────


def _kernel_settings_cfg(per_request: bool, profiles: bool = True) -> str:
    # 两态均显式写入 per_request_selection（F 默认 A1① 起为 True，关闭态不再依赖
    # "缺键=代码兜底 False" 的隐式语义）
    cfg = {"dsh": {"profile": "headless"}, "per_request_selection": bool(per_request)}
    if profiles:
        cfg["profiles"] = {"fast": {"dsh_profile": "siyi-fast"}, "deep": {"dsh_profile": "siyi-deep"}}
    return json.dumps(cfg)


def _kernel_ctx(agent_gateway=None, cfg: str | None = None):
    settings = types.SimpleNamespace(get_setting=lambda key, default=None: cfg if key == "agent_kernel" else default)
    return types.SimpleNamespace(agent_gateway=agent_gateway, settings_repository=settings)


def test_f_switch_off_ignores_params_byte_identical():
    """开关显式关闭（per_request_selection=False）：params 被忽略，行为与现状逐字节一致（返回注入单网关）。"""
    cfg = _kernel_settings_cfg(per_request=False)
    injected = DshGateway(profile="headless")
    ctx = _kernel_ctx(agent_gateway=injected, cfg=cfg)
    for params in ({"kernel": "claude_code"}, {"kernel": "codex"}, {"kernel": "deep"}, {"kernel": "fast"}, {"kernel": "auto"}, {"kernel": ""}, None):
        assert get_gateway(ctx, params) is injected


def test_f_switch_on_builds_concrete_kernel():
    """开关开启 + 具体内核：临时构建对应内核（不写 settings、不污染注入状态）。"""
    cfg = _kernel_settings_cfg(per_request=True)
    injected = DshGateway(profile="headless")
    ctx = _kernel_ctx(agent_gateway=injected, cfg=cfg)
    gw = get_gateway(ctx, {"kernel": "claude_code"})
    assert isinstance(gw, ClaudeCodeGateway)
    assert gw is not injected
    gw2 = get_gateway(ctx, {"kernel": "codex"})
    assert isinstance(gw2, CodexGateway)
    # 注入状态未被污染
    assert ctx.agent_gateway is injected


def test_f_switch_on_fast_deep_auto_fall_through_to_injected():
    """fast/deep 走现有 resolve_dsh_profile 路径；auto/空/其他回注入单网关。"""
    cfg = _kernel_settings_cfg(per_request=True)
    injected = DshGateway(profile="headless")
    ctx = _kernel_ctx(agent_gateway=injected, cfg=cfg)
    assert get_gateway(ctx, {"kernel": "deep"}) is injected
    assert get_gateway(ctx, {"kernel": "fast"}) is injected
    assert get_gateway(ctx, {"kernel": "auto"}) is injected
    assert get_gateway(ctx, {"kernel": ""}) is injected
    assert get_gateway(ctx, {"kernel": "nonsense"}) is injected
    assert get_gateway(ctx, {}) is injected
    assert get_gateway(ctx) is injected


# ── CT-12：内核经 gateway 执行 → usage source=kernel:<name>；shell 直调保持 shell ──


def test_ct12_kernel_usage_source_recorded(tmp_path):
    from ai.usage_tracker import init_usage_tracker, reset_usage_tracker_for_tests
    from backend.handlers.chat_paths.gateway_path import _drain_events

    reset_usage_tracker_for_tests()
    tracker = init_usage_tracker(str(tmp_path / "ai_usage.db"))
    try:
        # GIVEN 一个注入网关（替身内核 name=fake）+ 全局 UsageTracker
        gw = FakeKernelGateway(mode="ok")
        # WHEN 内核经 gateway 执行（drain 事件流）
        events = _drain_events(gw, "hello", None, None, None)
        # THEN 产生 result 事件，且用量记录 source=kernel:fake、by_source 出现对应行
        assert any(getattr(e, "kind", "") == "result" for e in events)
        records = tracker.get_records(limit=10)
        assert records, "内核经网关执行必须产生 usage 记录"
        assert records[0]["source"] == "kernel:fake"
        by_source = tracker.get_stats(days=7)["by_source"]
        assert any(r["source"] == "kernel:fake" and r["calls"] >= 1 for r in by_source)
        # AND shell 直调（不传 source）保持 'shell'
        tracker.record(provider="deepseek", model="deepseek-chat", prompt_tokens=5, completion_tokens=5)
        recs = tracker.get_records(limit=10)
        assert recs[0]["source"] == "shell"
    finally:
        reset_usage_tracker_for_tests()


# ── CT-13：契约 / 前端发送 / 后端 resolve 三表词汇一致性 ──
# 契约成本分级词汇（kernel-contract.md §2.4）
CONTRACT_TIER_VOCAB = ("fast", "deep", "shell", "legacy")
# 前端可发送词汇：ChatComposer COMPOSER_KERNEL_OPTIONS（auto/dsh/claude_code/codex）
# + ChatModule deepMode（deep）；'auto' 表示不指定内核
FRONTEND_SEND_VOCAB = ("auto", "dsh", "claude_code", "codex", "deep")
# 后端可 resolve 词汇：具体内核（get_gateway per-request）+ 成本分级（resolve_dsh_profile）
# + 兜底语义（auto/shell/legacy/未知 → 默认 profile / 注入网关，均为明确行为）
BACKEND_RESOLVE_VOCAB = ("dsh", "claude_code", "codex", "fast", "deep", "auto", "shell", "legacy")


def test_ct13_vocab_sets_mutually_consistent():
    # GIVEN 三表词汇；THEN 集合间一致性（改坏任一侧即红）
    concrete = {"dsh", "claude_code", "codex"}
    # 具体内核名与契约分级互斥（同一词汇不产生歧义）
    assert concrete.isdisjoint(set(CONTRACT_TIER_VOCAB))
    # 前端可发送词汇 ⊆（具体内核 ∪ 契约分级 ∪ auto）——后端对前端每个值都有明确语义
    assert set(FRONTEND_SEND_VOCAB) <= (concrete | set(CONTRACT_TIER_VOCAB) | {"auto"})
    # 后端 resolve 词汇覆盖前端可发送词汇与契约分级词汇
    assert set(FRONTEND_SEND_VOCAB) <= set(BACKEND_RESOLVE_VOCAB)
    assert set(CONTRACT_TIER_VOCAB) <= set(BACKEND_RESOLVE_VOCAB)


@pytest.mark.parametrize(
    "kernel",
    [
        "",
        "auto",
        "fast",
        "deep",
        "shell",
        "legacy",
        "dsh",
        "claude_code",
        "codex",
        "nonsense",
    ],
)
def test_ct13_kernel_vocab_resolve_behavior(kernel):
    """GIVEN 某内核词汇；WHEN 经 get_gateway + resolve_dsh_profile 解析；THEN 语义一致。

    改坏任一侧（前端发送词汇 / 后端 resolve 词汇 / 契约分级）测试即红。
    """
    cfg = _kernel_settings_cfg(per_request=True)
    injected = DshGateway(profile="headless")
    ctx = _kernel_ctx(agent_gateway=injected, cfg=cfg)
    params = {"kernel": kernel} if kernel else {}

    gw = get_gateway(ctx, params)
    profile = resolve_dsh_profile(ctx, params)
    k = kernel.strip().lower() if kernel else ""

    if k in ("dsh", "claude_code", "codex"):
        # 具体内核：开关开启时临时构建对应类型网关
        assert gw is not None and gw.name == k
        assert isinstance(gw, AGENT_GATEWAY_ADAPTERS[k])
    elif k in ("fast", "deep"):
        # 成本分级：走现有 resolve_dsh_profile 路径（注入网关 + profile 映射）
        assert gw is injected
        assert profile == {"fast": "siyi-fast", "deep": "siyi-deep"}[k]
    else:
        # auto / shell / legacy / 空 / 未知：返回注入单网关、profile 回默认
        assert gw is injected
        assert profile == "headless"


# ── H 项：MCP 工具策略层（M5 硬前置）──────────────────────────────

from services.tooling import tool_policy  # noqa: E402


def test_h_tool_policy_resolve_semantics():
    """resolve_policy 语义：allow_all 全放行；restrict 下白名单外 RW deny / DANGER require_approval / RO allow。"""
    p_allow_all = {"mode": "allow_all"}
    assert tool_policy.resolve_policy("RW", p_allow_all, "siyi_notes_create") == "allow"
    assert tool_policy.resolve_policy("DANGER", p_allow_all, "siyi_app_launch") == "allow"

    p_restrict = {"mode": "restrict", "allow": ["siyi_notes_search"]}
    # 白名单内放行
    assert tool_policy.resolve_policy("RO", p_restrict, "siyi_notes_search") == "allow"
    # 白名单外 RW → deny（无副作用）
    assert tool_policy.resolve_policy("RW", p_restrict, "siyi_notes_create") == "deny"
    # 白名单外 DANGER → require_approval（非 allow_all 必须走 ConfirmationStore）
    assert tool_policy.resolve_policy("DANGER", p_restrict, "siyi_backup_snapshot") == "require_approval"
    # RO 只读放行
    assert tool_policy.resolve_policy("RO", p_restrict, "siyi_tasks_list") == "allow"
    # deny 黑名单始终拦截
    p_deny = {"mode": "restrict", "deny": ["siyi_notes_get"]}
    assert tool_policy.resolve_policy("RO", p_deny, "siyi_notes_get") == "deny"


def test_h_tool_policy_per_kernel_whitelist():
    """per-kernel 白名单：指定内核用其白名单，其他内核不受影响。"""
    p = {"mode": "restrict", "allow": ["siyi_health"], "per_kernel": {"claude_code": {"allow": ["siyi_notes_create"]}}}
    # claude_code 白名单内 → allow
    assert tool_policy.resolve_policy("RW", p, "siyi_notes_create", kernel="claude_code") == "allow"
    # dsh（非白名单内核）→ deny
    assert tool_policy.resolve_policy("RW", p, "siyi_notes_create", kernel="dsh") == "deny"
    # 全局白名单对所有内核生效
    assert tool_policy.resolve_policy("RO", p, "siyi_health", kernel="dsh") == "allow"
    # 缺 kernel → 仅全局策略
    assert tool_policy.resolve_policy("RW", p, "siyi_notes_create") == "deny"


def test_h_tool_policy_parse_and_classify():
    """parse_mcp_tool_policy 规范化 + classify_tool_level 启发式。"""
    assert tool_policy.parse_mcp_tool_policy(None) == {"mode": "allow_all"}
    assert tool_policy.parse_mcp_tool_policy(json.dumps({"mode": "restrict"}))["mode"] == "restrict"
    assert tool_policy.parse_mcp_tool_policy("not-json") == {"mode": "allow_all"}
    assert tool_policy.classify_tool_level("siyi_notes_search") == "RO"
    assert tool_policy.classify_tool_level("siyi_notes_create") == "RW"
    assert tool_policy.classify_tool_level("siyi_app_launch") == "DANGER"
    assert tool_policy.classify_tool_level("siyi_health") == "RO"
    assert tool_policy.classify_tool_level("") == "RW"  # 缺声明视为 RW


# ── CT-11：MCP 工具策略门控（per-kernel 白名单，经 tools/call）────


def test_ct11_mcp_tool_policy_per_kernel(tmp_path, monkeypatch):
    """settings 配 per-kernel 白名单 → 白名单外工具拦截（无副作用）/ 白名单内放行 / DANGER require_approval。"""

    from mcp_server import tools as tools_mod
    from mcp_server.protocol import McpStdioServer
    from mcp_server.store import SiyiStore
    from mcp_server.tools import build_tool_specs
    from services.confirmation_store import ConfirmationStore

    store = SiyiStore(data_dir=str(tmp_path))
    policy = {
        "mode": "restrict",
        "allow": ["siyi_health"],  # 全局白名单
        "per_kernel": {"claude_code": {"allow": ["siyi_notes_search"]}},
    }
    # 隔离 ConfirmationStore（require_approval 走单例，需指向 tmp 数据目录）
    cs = ConfirmationStore(data_dir=str(tmp_path / "confirm"))
    cs.clear()

    class _FakeCSModule:
        """模拟 _load_confirmation_store 返回的真实模块（含 ConfirmationStore 类 + 单例访问器）。"""

        ConfirmationStore = ConfirmationStore

        @staticmethod
        def get_confirmation_store():
            return cs

    monkeypatch.setattr(tools_mod, "_load_confirmation_store", lambda: _FakeCSModule())

    specs = build_tool_specs(store, policy=policy, kernel="claude_code")
    srv = McpStdioServer(name="siyi", tools=specs)

    def _call(name, **args) -> dict:
        resp = srv.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )
        assert "error" not in resp, resp
        return json.loads(resp["result"]["content"][0]["text"])

    # 白名单内放行：per-kernel 白名单 siyi_notes_search（RO）→ 空结果
    assert _call("siyi_notes_search", query="hello") == []
    # 全局白名单 siyi_health → 放行
    assert _call("siyi_health")["server"] == "siyi-mcp"
    # 白名单外 RW 工具 → deny（明确错误，无副作用：不创建笔记）
    denied = _call("siyi_notes_create", title="x", content="y")
    assert denied["status"] == "denied"
    assert denied["tool"] == "siyi_notes_create"
    assert denied["level"] == "RW"
    assert "denied" in denied["error"]
    assert store.notes.list_notes() == []  # 无副作用
    # DANGER 非 allow_all → require_approval（走 ConfirmationStore）
    danger = _call("siyi_backup_snapshot", reason="test")
    assert danger["status"] == "requires_approval"
    assert danger.get("confirmation_id")
