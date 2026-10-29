# -*- coding: utf-8 -*-
"""批5 前后端对账（2026-09-18，分支 fix/p0-2026-06-18）。

覆盖 4 项（`deliverables/backlog-review-2026-09-18.md` §6 四行）：

1. **F-P2 漂移 ×3**（`transfer.list` / `usage.summary` / `task_queue.status`）
   —— 独立复核结论：**原报告描述不成立**。这三名字既不在后端注册表里，也**从未
   被任何前端调用**（P-C 探针报告自己也是"猜的名字"：它给出的更正
   `transfer.requests` 同样不存在）。真源 = 后端注册表；本文件补一道结构门禁
   「前端调用的方法名 ⊆ 后端注册名」，堵住这一类"报告造名"漂移。
2. **mcp:kernel 两真源矛盾**（跨窗续跑）—— 真源收敛到内核能力声明
   `capabilities()["resume"]`（后端 `protocol.supports_resume`，前端经
   `kernel.status.details.capabilities.resume`）。
3. **provider 写后热生效** —— 写入换 client 后主动失效 butler agent 缓存，
   且缓存命中额外校验 client 身份（写入后立刻读必得新值，不等 TTL）。
4. **F-P1-1 队列2 端到端**（butler CONFIRM）—— 走**真实 RPC 链路**
   （`MethodRegistry.dispatch`，即 ws_server 的下游同一条路径）复验
   「确认入队 → 审批/裁决 → 落库」。**降级说明见 §4 文件头注释**：无真实后端
   进程 + 真实 LLM（离线环境不可得），故为"可复现的契约级端到端"，非 WS 套接字级。

隔离纪律：全程不碰生产 data/（确认队列用 tmp_path 单例；butler checkpoint 走
内存 data_store 桩），符合 tests/conftest.py 的生产数据泄漏守卫。
"""

from __future__ import annotations

import json
import os
import re
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# ═══════════════════════════════════════════════════════════════════════════
# §1 F-P2 漂移 ×3：对账「前端调用名」与「后端注册名」
# ═══════════════════════════════════════════════════════════════════════════

# 报告声称 -32601 的三个方法名（P-C 探针 §5 / backlog §6）。
_REPORTED_PHANTOM_NAMES = ("transfer.list", "usage.summary", "task_queue.status")

# 后端**静态**注册名扫描（与 scripts/check_method_coverage.py 的 REG_RE 同源口径）。
_REGISTER_RE = re.compile(r"""\bregister(?:_with_schema)?\s*\(\s*["']([a-zA-Z][a-zA-Z0-9_.]+)["']""")

# 前端真实 RPC 调用形态（与 scripts/check_method_coverage.py 的 CALL_PATTERNS 同源）。
_CALL_RE = re.compile(
    r"""\b(?:client|wsClient|ws|syncCall|invoke|router|bridge|callRpc|api)"""
    r"""\.\w*(?:call|invoke|stream|streamTyped)\s*(?:<[^()]*>)?\s*\(\s*["']([a-zA-Z][a-zA-Z0-9_.]+)["']"""
)

# 跨行调用：``client.call<T>(\n  'method.name',\n  {...}\n)`` —— 单行正则看不见。
# 实测仓内确有此类写法（NavSidebar.tsx:135 的 task_queue.stats、skin.*、system.health
# 等 7 处），不补这层会把"已接线"误判为"零调用"，也会漏掉真正的漂移。
# 泛型用 ``[^()]*`` 而非 ``[^>]*``：``Record<string, number>`` 这类嵌套泛型内含 '>'，
# 用 ``[^>]*`` 会在内层 '>' 处截断从而整条漏掉（实测踩过）。
_CALL_OPEN_RE = re.compile(r"""\.\w*(?:call|invoke|stream|streamTyped)\b[^(]*\(\s*$""")
_CALL_ARG_LITERAL_RE = re.compile(r"""^\s*["']([a-zA-Z][a-zA-Z0-9_.]+)["']""")

# 动态注册前缀（后端以循环批量注册的方法族）——沿用 check_method_coverage.py 的
# DYNAMIC_PREFIXES 口径：静态扫描拿不到的名字在这些前缀下不算漂移。
_DYNAMIC_PREFIXES = ("cron.", "job_hunter.", "rss.", "process.", "timeline.", "task_queue.")

_SKIP_DIRS = {
    "node_modules",
    "dist",
    "build",
    ".next",
    "coverage",
    "web_dist",
    "assets",
    "__pycache__",
    "archive",
    ".git",
    "release",
    "test-results",
}


def _scan_registered_methods() -> set[str]:
    """扫描后端静态注册的方法名集合（注册表盘面 = 唯一真源）。"""
    registered: set[str] = set()
    backend = REPO_ROOT / "思忆集app" / "backend"
    for py in backend.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        registered.update(_REGISTER_RE.findall(text))
    return registered


def _scan_frontend_calls() -> dict[str, list[str]]:
    """扫描前端（desk-notes + 手机端）真实调用点：方法名 → ["相对路径:行号"]。"""
    hits: dict[str, list[str]] = {}
    for pkg in ("desk-notes", "思忆集手机端app"):
        root = REPO_ROOT / pkg
        if not root.exists():
            continue
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in _SKIP_DIRS]
            for name in fn:
                if not name.endswith((".ts", ".tsx", ".js", ".jsx", ".vue")):
                    continue
                full = Path(dp) / name
                try:
                    lines = full.read_text(encoding="utf-8", errors="ignore").splitlines()
                except OSError:
                    continue
                for lineno, raw in enumerate(lines, 1):
                    stripped = raw.strip()
                    if stripped.startswith(("//", "*", "#")):
                        continue
                    for method in _CALL_RE.findall(raw):
                        hits.setdefault(method, []).append(f"{full.relative_to(REPO_ROOT)}:{lineno}")
                    # 跨行形态：调用括号收尾 → 向后最多 3 行找首个方法名字面量
                    if _CALL_OPEN_RE.search(raw):
                        for nxt in range(lineno, min(lineno + 3, len(lines))):
                            arg = _CALL_ARG_LITERAL_RE.match(lines[nxt])
                            if arg:
                                hits.setdefault(arg.group(1), []).append(f"{full.relative_to(REPO_ROOT)}:{nxt + 1}")
                                break
    return hits


def test_fp2_reported_three_names_are_phantom_not_drift():
    """报告口径证伪：三项**既未注册、也从未被前端调用**（反转说明见下）。

    反转说明（批5，2026-09-18）：backlog §6「F-P2 漂移 ×3」与 P-C 探针 §5 把
    ``transfer.list`` / ``usage.summary`` / ``task_queue.status`` 记为"方法名漂移
    （前端名 vs 后端名不符）"。独立复核（本测试 + 后端注册表扫描）证明：这三个
    名字**没有任何前端调用点**，因此不存在"两边对不上"的漂移；它们是探针自造的
    抽样名单。故本断言把口径反转为**正向契约**：真源（注册表）里没有它们，前端
    也不调它们——若将来真有人这么写，会先在下方 test_frontend_rpc_names_... 变红。

    历史事实（反转前勘察结论，保留供追溯）：P-C 报告 §5 原文为"不可达 3/15:
    transfer.list / usage.summary / task_queue.status → -32601 Method not found
    （P-E 报告中的方法名与后端注册名漂移，如 transfer 面实名含 transfer.requests 等）"。
    复核实测：``transfer.requests`` 同样**不在**注册表里——报告给的"更正名"也是
    猜的。真正存在的是 ``transfer.history`` / ``transfer.files`` 等（见下一测试）。
    """
    registered = _scan_registered_methods()
    frontend_calls = _scan_frontend_calls()

    for name in _REPORTED_PHANTOM_NAMES:
        assert name not in registered, f"{name} 竟然后端已注册？请重核本报告口径"
        assert name not in frontend_calls, f"{name} 竟有前端调用点：{frontend_calls.get(name)}"

    # 探针报告给出的"更正名"同样是幻觉——一并钉死，避免下次又被引用。
    assert "transfer.requests" not in registered


def test_fp2_real_names_for_the_three_areas_are_registered_and_used():
    """真源盘面：三领域**实际**存在且被前端使用的名字（对照上面的幻名）。"""
    registered = _scan_registered_methods()
    frontend_calls = _scan_frontend_calls()

    for real_name in ("transfer.history", "usage.today", "usage.dashboard", "task_queue.stats", "task_queue.list"):
        assert real_name in registered, f"{real_name} 未注册（真源盘面异常）"
        assert real_name in frontend_calls, f"{real_name} 注册了但前端零调用（僵尸接口？）"


def test_frontend_rpc_names_all_registered_backend_side():
    """结构门禁：前端调用的每个方法名都必须在后端注册表里（堵"报告造名/改名"类漂移）。

    真源 = 后端注册表（静态注册扫描）。动态前缀族（cron./rss./job_hunter./
    process./timeline./task_queue.）沿用既有口径豁免——它们的名字由循环生成，
    静态扫描天然看不见（与 scripts/check_method_coverage.py 的 DYNAMIC_PREFIXES 一致）。
    """
    registered = _scan_registered_methods()
    frontend_calls = _scan_frontend_calls()
    assert frontend_calls, "前端调用点扫描为空 —— 路径口径坏了（假绿）"

    missing = sorted(name for name in frontend_calls if name not in registered and not name.startswith(_DYNAMIC_PREFIXES))
    detail = "\n".join(f"  - {n}  调用点: {frontend_calls[n][:2]}" for n in missing)
    assert not missing, f"前端调用了后端未注册的方法名（真源 = 后端注册表）：\n{detail}"


# ═══════════════════════════════════════════════════════════════════════════
# §2 mcp:kernel 跨窗续跑：单一真源 = 内核能力声明 capabilities()["resume"]
# ═══════════════════════════════════════════════════════════════════════════


class _CapGateway:
    """按声明驱动 capabilities() 的极简内核桩。"""

    def __init__(self, resume: bool, name: str = "dsh") -> None:
        self.name = name
        self._resume = resume
        self.resume_calls: list[str] = []

    def capabilities(self) -> dict[str, bool]:
        return {"stream": False, "tool": False, "confirm": False, "resume": self._resume, "cancel": False}

    def resume(self, session_ref, prompt, timeout_s=None, profile=None):  # pragma: no cover
        self.resume_calls.append(session_ref)
        raise AssertionError("未声明 resume 能力的内核不得被调用 resume")


def test_protocol_supports_resume_reads_capability_declaration():
    """基元：``supports_resume`` 只认能力声明，缺声明一律保守 False。"""
    from services.agent_gateway.protocol import BaseGateway, supports_resume

    assert supports_resume(_CapGateway(True)) is True
    assert supports_resume(_CapGateway(False)) is False
    # 真实 DSH 适配器（继承 BaseGateway 默认 _default_capabilities，resume=False）
    from services.agent_gateway.adapters import DshGateway

    assert supports_resume(DshGateway()) is False
    # 裸对象（无 capabilities / supports_resume）→ 保守 False
    assert supports_resume(types.SimpleNamespace(name="bare")) is False
    assert supports_resume(None) is False

    # BaseGateway 自身的默认声明也是 False
    class _Bare(BaseGateway):
        def submit(self, prompt, timeout_s=None, profile=None):  # pragma: no cover
            raise NotImplementedError

        def health(self):  # pragma: no cover
            return {}

    assert supports_resume(_Bare()) is False


def test_auto_gateway_delegates_capabilities_to_active_kernel():
    """auto 档也必须透出**真实生效内核**的能力声明（否则前端读到的还是假值）。"""
    from services.agent_gateway.auto import AutoGateway

    gw = AutoGateway(candidates=["dsh"])
    gw._pool["dsh"] = _CapGateway(True, name="dsh")
    gw._active = "dsh"
    assert gw.capabilities()["resume"] is True, "auto 未把能力声明委托给 active 内核"

    gw._active = None  # none 态：回默认声明（全 False）
    assert gw.capabilities()["resume"] is False


def test_kernel_status_exposes_same_resume_flag_both_sides_read():
    """「同一处」证明：前端读的 kernel.status 与后端判据来自同一份能力声明。"""
    from backend.handlers._kernel import build_kernel_status
    from services.agent_gateway.protocol import supports_resume

    settings = types.SimpleNamespace(get_setting=lambda key, default=None: "gateway" if key == "chat_route" else default)
    for declared in (True, False):
        gw = _CapGateway(declared)
        ctx = types.SimpleNamespace(agent_gateway=gw, settings_repository=settings)
        status = build_kernel_status(ctx)
        assert status["details"]["capabilities"]["resume"] is declared
        # 后端判据与暴露给前端的值必须一致（否则又是两真源）
        assert supports_resume(gw) is declared


async def test_gateway_resume_does_not_fake_resume_for_capability_less_kernel():
    """红→绿核心：未声明 resume 的内核不得被当作可续跑（旧实现无条件调 gw.resume）。

    红（修复前）：``handled is True`` 且 ``gw.resume`` 被调用（DSH 的 resume 实为
    全新 submit 会话 → 一次"续跑"变成新会话重跑）；
    绿（修复后）：``handled is False``、零 chunk、``resume`` 零调用，chat.resume
    回落到 butler 续跑（无挂起 checkpoint 时如实回 no_pending_task）。
    """
    from backend.handlers.chat_paths import run_gateway_resume

    class _Collector:
        def __init__(self) -> None:
            self.chunks: list[Any] = []

        async def __call__(self, text, chunk_type=None):
            self.chunks.append((text, chunk_type))

    settings = types.SimpleNamespace(get_setting=lambda key, default=None: default)
    gw = _CapGateway(False)
    ctx = types.SimpleNamespace(agent_gateway=gw, settings_repository=settings)
    collector = _Collector()

    handled = await run_gateway_resume(ctx, {"session_id": "s1", "message": "继续"}, collector)

    assert handled is False, "未声明 resume 能力的内核不得被当作可续跑"
    assert collector.chunks == [], "不得产出任何 chunk（不得伪造一次续跑）"
    assert gw.resume_calls == [], "不得调用 gw.resume（否则实为新会话重跑）"


# ═══════════════════════════════════════════════════════════════════════════
# §3 provider 写后热生效：换 client 后 agent 缓存必须立刻失效
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def butler_mod():
    """butler **真实**模块（非兼容 shim）+ 缓存清理。

    用 ``assistant.agents.butler_agent`` 而非 ``assistant.butler_agent``：后者是
    ``import *`` 兼容层，私有符号（``_build_agent``）不经 star-import 导出，
    monkeypatch 会落空。事件订阅在真实模块导入时完成，勿 ``event_bus.clear()``。
    """
    from assistant.agents import butler_agent as mod

    mod.invalidate_butler_agent_cache()
    yield mod
    mod.invalidate_butler_agent_cache()


def _patch_build(monkeypatch, mod) -> list[Any]:
    """把 _build_agent 换成记录用的桩，避免真实 PydanticAI Agent 构建（慢）。"""
    built: list[Any] = []

    def _fake_build(context):
        built.append(context.ai_client)
        return types.SimpleNamespace(bound_client=context.ai_client)

    monkeypatch.setattr(mod, "_build_agent", _fake_build)
    return built


def test_agent_cache_rebuilt_immediately_after_ai_client_swap(monkeypatch, butler_mod):
    """红→绿核心（F-P2 provider 热生效）：写入换 client 后**立刻**读到新 agent。

    红（修复前）：缓存 key 只有 ``id(context)``，TTL（1800s）内命中旧条目 → 第二次
    读取仍返回旧 agent（旧 client 闭包在里面）→ ``a2 is a1`` 断言失败；
    绿（修复后）：命中条件追加「条目绑定的 client is 当前 context.ai_client」，
    写入后立刻读必得新值，无需等 TTL、无需重启后端。
    """
    mod = butler_mod
    built = _patch_build(monkeypatch, mod)

    client_a = object()
    client_b = object()
    ctx = types.SimpleNamespace(ai_client=client_a)

    first = mod.get_butler_agent(ctx)
    assert first is not None
    assert mod.get_butler_agent(ctx) is first, "同 client 应命中缓存（不重复构建）"
    assert len(built) == 1

    # 模拟 provider.add/update/remove → context.recreate_ai_client() 换掉 client
    ctx.ai_client = client_b
    second = mod.get_butler_agent(ctx)

    assert second is not None
    assert second is not first, "写入后立刻读必须拿到新值（而不是等 TTL）"
    assert second.bound_client is client_b, "新 agent 必须绑定新 client"
    assert len(built) == 2


def test_invalidate_butler_agent_cache_is_explicit_drop_interface(butler_mod, monkeypatch):
    """显式失效接口：清空并返回条目数；清空后下一次读取必重建。"""
    mod = butler_mod
    built = _patch_build(monkeypatch, mod)
    ctx = types.SimpleNamespace(ai_client=object())

    mod.get_butler_agent(ctx)
    assert mod.invalidate_butler_agent_cache() == 1
    assert mod.invalidate_butler_agent_cache() == 0, "幂等：空缓存再清返回 0"
    mod.get_butler_agent(ctx)
    assert len(built) == 2, "清空后必须重建"


def test_ai_client_recreated_event_invalidates_cache(butler_mod, monkeypatch):
    """依赖倒置接线：``ai_client.recreated`` 事件 → 缓存主动失效。"""
    from core.events import AppEvent, event_bus

    mod = butler_mod
    _patch_build(monkeypatch, mod)
    mod.get_butler_agent(types.SimpleNamespace(ai_client=object()))
    assert mod._agent_cache, "前置条件：缓存里应有条目"

    event_bus.emit(AppEvent(event_type="ai_client.recreated", payload={"source": "test"}))

    assert not mod._agent_cache, "收到 ai_client.recreated 后缓存应为空（主动失效）"


def test_recreate_ai_client_emits_invalidation_event(monkeypatch):
    """写入路径接线：``AppContext.recreate_ai_client`` 必须发出 ai_client.recreated。

    provider.add/update/remove 三个 handler 都经 ``_recreate_ai_client()`` →
    ``context.recreate_ai_client()``（单一咽喉），故此处一条断言覆盖三条写入路径。
    """
    from app_runtime.app_context import AppContext
    from core.events import event_bus

    ctx = AppContext.__new__(AppContext)  # 只测该方法，不跑完整 DI 装配
    ctx._domain_facades = {}
    ctx._job_hunter_service = MagicMock()  # 唯一惰性 property，预置避免触发真实构建
    new_client = object()
    ctx.ai_manager = MagicMock()
    ctx.ai_manager.recreate_ai_client.return_value = new_client
    ctx.service_registry_manager = MagicMock()
    ctx.memory_manager = MagicMock()
    ctx.settings_repository = MagicMock()

    seen: list[Any] = []
    handler = lambda ev: seen.append(ev)  # noqa: E731
    event_bus.on("ai_client.recreated", handler)
    try:
        ctx.recreate_ai_client()
    finally:
        event_bus.off("ai_client.recreated", handler)

    assert ctx.ai_client is new_client, "新 client 必须已注入"
    assert len(seen) == 1, "recreate_ai_client 未发出 ai_client.recreated（provider 热生效断链）"


# ═══════════════════════════════════════════════════════════════════════════
# §4 F-P1-1 队列2（butler CONFIRM）端到端复验
# ═══════════════════════════════════════════════════════════════════════════
#
# 降级说明（如实）：本环境无真实后端进程 + 无真实 LLM（离线），无法做 WS 套接字级
# 端到端。此处**不假装**做了，而是走**真实 RPC 链路的下游同一路径**：
#   MethodRegistry.dispatch（ws_server 收到 JSON-RPC 后调的就是它）
#     → _confirmations.handle_confirmation_resolve（含 schema 校验/理由必填）
#     → services.confirmation_flow.resolve_confirmation（编排）
#     → butler 注册的真实执行 handler（capability_router.execute_direct）
#     → ConfirmationStore 落盘 + action.confirm_resolved 事件
# 未覆盖：aiohttp WS 帧编解码、鉴权握手、前端卡片渲染（前者由 smoke/ws_*.py
# 探针覆盖，后者由 desk-notes 的 vitest floating-confirm-contract 覆盖）。
# 两侧合起来才是"端到端"；本文件负责其中后端真链路段，且可复现（离线、无网）。


class _ScriptedClient:
    """第一轮 shell_run（触发 CONFIRM）→ 第二轮 notes_list → 最终汇报。"""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    @staticmethod
    def _tool(name: str, args: dict, cid: str) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
                    }
                }
            ],
            "model": "scripted",
        }

    def chat_completion(self, messages, **kwargs):
        self.calls.append((list(messages), dict(kwargs)))
        if len(self.calls) == 1:
            return self._tool("shell_run", {"command": "echo hi"}, "call_shell")
        return {"choices": [{"message": {"content": "任务完成：已处理。", "tool_calls": None}}], "model": "scripted"}


class _FakeRouter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, bool]] = []

    async def execute_direct(self, action_id, args, approved=False):
        self.calls.append((action_id, dict(args or {}), approved))
        return {"stdout": "hi", "exit_code": 0} if action_id == "shell.run" else {"ok": True}


@pytest.fixture()
def queue2_env(tmp_path, monkeypatch):
    """隔离装配：确认队列落 tmp_path、butler checkpoint 走内存 data_store。"""
    import services.confirmation_store as cs_mod
    from assistant.agents import butler_agent as butler
    from assistant.checkpoint import CheckpointManager

    cs_mod._store_singleton = cs_mod.ConfirmationStore(str(tmp_path))
    butler.invalidate_butler_agent_cache()
    butler._pending_contexts.clear()

    mem: list = []
    ds = MagicMock()
    ds.load_json.side_effect = lambda k, d: list(mem)
    ds.update_json.side_effect = lambda k, d, u: mem.__setitem__(slice(None), u(list(mem))) or mem
    cm = CheckpointManager(ds, max_versions=5)
    tm = types.SimpleNamespace(checkpoint_manager=cm)
    ctx = types.SimpleNamespace(
        automation=types.SimpleNamespace(autonomous_assistant_manager=types.SimpleNamespace(task_manager=tm)),
        data_store=ds,
        ai_client=_ScriptedClient(),
        capability_router=_FakeRouter(),
        mcp_client_manager=None,
        settings_repository=MagicMock(),
    )
    yield types.SimpleNamespace(ctx=ctx, store_dir=tmp_path, butler=butler, cs_mod=cs_mod)

    butler.invalidate_butler_agent_cache()
    butler._pending_contexts.clear()
    cs_mod._store_singleton = None


def _build_queue2_registry(ctx):
    """真实注册面：confirmation.* 三个 RPC（与生产 register_all 同源）。"""
    from backend.handlers._confirmations import register_confirmations
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_confirmations(registry, ctx)
    return registry


async def _collect(agen):
    out = []
    async for chunk in agen:
        out.append(chunk)
    return out


async def test_queue2_end_to_end_enqueue_approve_persist(queue2_env):
    """队列2 端到端（批准）：入队 → RPC 审批 → 真实执行 → 落库 → 定案事件。

    这是 F-P1-1 缺失的"动态复验"：前端 vitest 只证到"点了批准会发 RPC"，
    这里补证"RPC 发出去之后后端真的执行并落库"。
    """
    from core.events import event_bus

    ctx = queue2_env.ctx
    store_dir = queue2_env.store_dir

    # ① 入队：真实 agent 循环触发 shell.run CONFIRM，前端订阅的 action.confirm_required 必须发出
    required: list[Any] = []
    handler = lambda ev: required.append(ev)  # noqa: E731
    event_bus.on("action.confirm_required", handler)
    try:
        from backend.handlers.chat_paths.pydantic_agent_path import run_pydantic_agent_path

        chunks = await _collect(run_pydantic_agent_path(ctx, "跑个命令", {"session_id": "sess-q2"}, [], None))
    finally:
        event_bus.off("action.confirm_required", handler)

    assert chunks == [], "挂起时应无最终文本"
    assert len(required) == 1, "队列2 入队事件未发出（前端将永远看不到卡片）"
    payload = required[0].payload
    assert payload["source"] == "butler", "队列2 butler 分支：source 必须为 butler"
    assert payload["action_id"] == "shell.run"
    cid = payload["confirmation_id"]

    # ② 审批前：经真实 RPC 查状态 = pending（前端卡片数据源）
    registry = _build_queue2_registry(ctx)
    status = await registry.dispatch("confirmation.status", {"confirmation_id": cid})
    assert status["status"] == "pending"
    assert status["source"] == "butler"

    # ③ 裁决：走真实 RPC 链路（含 schema 校验 + 理由必填闸）
    resolved: list[Any] = []
    rhandler = lambda ev: resolved.append(ev)  # noqa: E731
    event_bus.on("action.confirm_resolved", rhandler)
    try:
        result = await registry.dispatch(
            "confirmation.resolve",
            {"confirmation_id": cid, "approved": True, "reason": "需要跑这条命令"},
        )
    finally:
        event_bus.off("action.confirm_resolved", rhandler)

    assert result["success"] is True and result["approved"] is True
    # 真实执行（approved=True 透传给 ActionGuard）
    assert ctx.capability_router.calls == [("shell.run", {"command": "echo hi"}, True)]
    # ④ 定案事件（两窗卡片翻转的数据源）
    assert len(resolved) == 1 and resolved[0].payload["approved"] is True

    # ⑤ 落库：确认队列与 butler checkpoint 双落盘
    persisted = json.loads((store_dir / "confirmations.json").read_text(encoding="utf-8"))
    assert persisted[cid]["status"] == "approved"
    assert persisted[cid]["result"]["stdout"] == "hi"

    from assistant.butler_checkpoint import load_butler_checkpoint

    cp = load_butler_checkpoint(ctx, "sess-q2")
    assert cp is not None and cp["context_snapshot"]["confirmations"][0]["status"] == "approved"

    # ⑥ 一次性消费：重复 resolve 必须被拒（前端据此把卡片翻 expired，不死循环）
    again = await registry.dispatch(
        "confirmation.resolve",
        {"confirmation_id": cid, "approved": True, "reason": "再来一次"},
    )
    assert again == {"success": False, "reason": "confirmation_not_found_or_expired"}


async def test_queue2_end_to_end_reject_does_not_execute(queue2_env):
    """队列2 端到端（拒绝）：RPC 裁决拒绝 → 不执行工具 → 拒绝结果落库。"""
    from core.events import event_bus

    ctx = queue2_env.ctx
    required: list[Any] = []
    handler = lambda ev: required.append(ev)  # noqa: E731
    event_bus.on("action.confirm_required", handler)
    try:
        from backend.handlers.chat_paths.pydantic_agent_path import run_pydantic_agent_path

        await _collect(run_pydantic_agent_path(ctx, "跑个命令", {"session_id": "sess-q2r"}, [], None))
    finally:
        event_bus.off("action.confirm_required", handler)
    cid = required[0].payload["confirmation_id"]

    registry = _build_queue2_registry(ctx)
    result = await registry.dispatch("confirmation.resolve", {"confirmation_id": cid, "approved": False})

    assert result["success"] is True and result["approved"] is False
    assert ctx.capability_router.calls == [], "拒绝时不得执行工具"
    persisted = json.loads((queue2_env.store_dir / "confirmations.json").read_text(encoding="utf-8"))
    assert persisted[cid]["status"] == "rejected"


async def test_queue2_rpc_rejects_approval_without_reason(queue2_env):
    """RPC 层契约：批准必带理由（前端闸 + 后端闸双保险的后端侧）。"""
    ctx = queue2_env.ctx
    registry = _build_queue2_registry(ctx)
    # 无确认条目时也会先撞理由闸（理由校验在 store 查询之前）
    result = await registry.dispatch("confirmation.resolve", {"confirmation_id": "whatever", "approved": True})
    assert result["success"] is False
    assert result["reason"] == "approval_reason_required"


async def test_queue2_rpc_requires_confirmation_id(queue2_env):
    """RPC 层 schema：缺 confirmation_id → 干净 -32602（而非 handler 内 TypeError）。"""
    from core.errors import InvalidParamsError

    registry = _build_queue2_registry(queue2_env.ctx)
    with pytest.raises(InvalidParamsError):
        await registry.dispatch("confirmation.resolve", {"approved": True, "reason": "x"})
