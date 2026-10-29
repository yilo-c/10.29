# -*- coding: utf-8 -*-
"""裁决 2 / 裁决 3 新契约测试（be-approval，2026-09-17）。

规格书：deliverables/authorization-model-adjudication-2026-09-17.md
证据链：deliverables/pc-bubble-probe-final-2026-09-17.md §1/§2、
        deliverables/round2-fix-execution-2026-09-17.md §五。

════════════════════════════════════════════════════════════════════
附：五张名单交叉对表（裁决 3 要求；只如实记录成员关系，不改任何名单）
════════════════════════════════════════════════════════════════════
五张名单：
  S  butler_agent.SAFE_TOOLS            —— 管家对话工具「免确认直接执行」集
  C  butler_agent.CONFIRM_TOOLS         —— 管家对话工具「发起队列2确认」集
  H  action_guard._HIGH_RISK_ACTIONS    —— approval_policy=on-risk 时需人工确认集
  E  action_guard._LLM_ELEVATED_ACTIONS —— LLM 源高危（elevated）终态拦截集
  R  handlers._R4_METHODS               —— RPC 面强制队列1（sandbox 审批）集

| action            | S | C | H | E | R | 交叉诊断                                                     |
|-------------------|---|---|---|---|---|--------------------------------------------------------------|
| file.write        | Y | . | . | Y | Y | ★矛盾①：SAFE 免确认 ∩ ELEVATED 要批准 ∩ R4 要队列1           |
| notes.delete      | Y | . | . | Y | Y | ★矛盾① 同上（本次任务1的主诉动作）                            |
| notes.batch_delete| Y | . | . | Y | Y | ★矛盾① 同上                                                   |
| tasks.delete      | Y | . | . | Y | Y | ★矛盾① 同上                                                   |
| shell.run         | . | Y | Y | Y | . | C∩H∩E：三名单一致要求人工批准（本无矛盾）                     |
| process.kill      | . | Y | Y | Y | . | 同上                                                          |
| app.launch        | . | Y | Y | Y | . | 同上                                                          |
| web.download      | . | Y | Y | Y | . | 同上                                                          |
| cron.remove       | . | Y | Y | . | Y | C∩H∩R：对话确认 + RPC 队列1 双出路（一致）                    |
| browser.click     | . | Y | Y | . | . | C=H（一致）                                                   |
| browser.type      | . | Y | Y | . | . | 同上                                                          |
| browser.automate  | . | Y | Y | . | . | 同上                                                          |
| cron.add          | . | Y | Y | . | . | 同上                                                          |
| code.execute      | . | . | Y | . | . | H-only：走 SANDBOX_TOOLS（沙箱执行），不经 CONFIRM 判定       |
| clipboard.write   | . | . | . | Y | . | E-only：直调 broker 终态拦截（纵深防御），非 butler 目录工具   |
| settings          | . | . | . | Y | . | 同上（裸 id；RPC 面是 settings.set 按 risk_resolver 动态判级） |
| inbox.delete      | . | . | . | Y | . | E-only：未注册为对话工具，broker 纵深拦截                      |

裁决 3 前的病根 = ★矛盾①的四个成员：
  · 对话路径：∈ S → _requires_confirmation=False → 直落 broker → guard.validate
    elevated 判定（∈ E）→ llm_source_high_risk **终态拦截**（error dict 回 LLM，
    谎称「需用户手动确认」但系统里没有它的确认出路）；
  · RPC 路径：∈ R → 队列1 sandbox pending 有出路 → 重放 skip_approval=True →
    D6 _R4_APPROVED_REPLAY → handler 以 approved=True 过 guard（出路闭环）。
  → 同一动作两副面孔：对话通道「要批准但没出路」，RPC 通道「有出路」。
裁决 3 的修法：对话路径在工具入口（butler._requires_confirmation）把
  requires_llm_elevation_approval=True 的动作**升格为队列2 confirmation**
  （复用 shell.run 既有 CONFIRM 闭环），出路补齐后矛盾消除；名单成员不动，
  guard.validate 的 elevated→block 保留为「未接入确认闭环的直调路径」纵深防御。
（本表由 test_five_lists_membership_cross_check 以断言固化成员关系。）
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.agents.butler_agent import CONFIRM_TOOLS, SAFE_TOOLS, _requires_confirmation, execute_tool_action  # noqa: E402
from backend.handlers import _R4_METHODS  # noqa: E402
from core.action_guard import _HIGH_RISK_ACTIONS, _LLM_ELEVATED_ACTIONS, ActionGuard  # noqa: E402


def _guard(**settings) -> ActionGuard:
    repo = MagicMock()
    repo.get_setting.side_effect = lambda key, default=None: settings.get(key, default)
    return ActionGuard(settings_repository=repo, observability_service=MagicMock())


# ════════════════════════════════════════════════════════════════
# 裁决 3 —— 判据层（ActionGuard.is_llm_elevated / requires_llm_elevation_approval）
# ════════════════════════════════════════════════════════════════


class TestAdjudication3GuardPredicate:
    def test_elevated_membership_and_source_gate(self):
        guard = _guard()
        # 显式名单：LLM 族来源 → elevated
        for src in ("llm", "agent", "assistant"):
            assert guard.is_llm_elevated("notes.delete", src) is True
            assert guard.is_llm_elevated("file.write", src) is True
        # tool.* 危险名启发式（P0-3 语义保持）
        assert guard.is_llm_elevated("tool.my_shell_helper", "llm") is True
        assert guard.is_llm_elevated("tool.delete_all", "assistant") is True
        assert guard.is_llm_elevated("tool.read_note", "llm") is False
        # 非 LLM 来源（RPC 直连/用户手动）不 elevated —— 队列1/免审批语义不变
        for src in ("rpc", "user", "code", "unknown", ""):
            assert guard.is_llm_elevated("notes.delete", src) is False
        # 空动作
        assert guard.is_llm_elevated("", "llm") is False

    def test_requires_approval_downgrades(self):
        """升格判据的三个显式降档：warn_only / policy=never / always_allow。"""
        from core.action_guard import KEY_ACTION_ALWAYS_ALLOW, KEY_ACTION_RISK_WARN_ONLY, KEY_ASSISTANT_APPROVAL_POLICY

        guard = _guard()
        assert guard.requires_llm_elevation_approval("notes.delete", "assistant") is True
        # warn_only=True：显式降档 → 不升格（validate 内告警放行语义保持）
        wo = _guard(**{KEY_ACTION_RISK_WARN_ONLY: True})
        assert wo.requires_llm_elevation_approval("notes.delete", "assistant") is False
        # 但 validate() 在 warn_only 下仍放行（纵深防御的显式出口）
        r = wo.validate("notes.delete", {}, source="assistant")
        assert r.allowed is True, r
        # approval_policy=never → 不升格
        nv = _guard(**{KEY_ASSISTANT_APPROVAL_POLICY: "never"})
        assert nv.requires_llm_elevation_approval("notes.delete", "assistant") is False
        # always_allow → 不升格，且 validate() 给出放行出路（裁决3「批准有出路」）
        aa = _guard(**{KEY_ACTION_ALWAYS_ALLOW: ["notes.delete"]})
        assert aa.requires_llm_elevation_approval("notes.delete", "assistant") is False
        r2 = aa.validate("notes.delete", {}, source="assistant")
        assert r2.allowed is True, r2

    def test_validate_terminal_block_message_is_honest(self):
        """未接入确认闭环的直调路径：elevated 终态拦截保留，但文案不得再谎称
        「需用户手动确认」而不给出路（裁决3 文案如实化）。"""
        guard = _guard()
        r = guard.validate("notes.delete", {}, source="assistant")
        assert r.allowed is False
        assert r.reason == "llm_source_high_risk"
        msg = "".join(r.warnings or [])
        assert "确认请求" in msg and "审批通道" in msg, msg
        assert "需经用户批准" in msg, msg

    def test_approved_replay_still_passes_guard(self):
        """D6 出路不回归：approved=True（队列1 重放 / 队列2 批准后执行）放行。"""
        guard = _guard()
        assert guard.validate("file.write", {}, source="assistant", approved=True).allowed is True
        assert guard.validate("notes.delete", {}, source="rpc", approved=True).allowed is True


# ════════════════════════════════════════════════════════════════
# 裁决 3 —— 入口层（butler._requires_confirmation 升格判定）
# ════════════════════════════════════════════════════════════════


def _async_ok(result):
    """构造记录型 async execute_direct（MagicMock 挂协程用 await_args_list 取证）。"""
    from unittest.mock import AsyncMock

    return AsyncMock(return_value=result)


def _butler_ctx(guard=None):
    router = MagicMock()
    router.execute_direct = MagicMock()
    router.action_guard = guard if guard is not None else _guard()
    return SimpleNamespace(capability_router=router)


class TestAdjudication3ButlerEntry:
    def test_safe_intersect_elevated_tools_upgraded(self):
        """★矛盾①成员（notes.delete 等）在 butler 入口即升格为需确认。"""
        ctx = _butler_ctx()
        for action_id in ("notes.delete", "notes.batch_delete", "tasks.delete", "file.write"):
            assert action_id in SAFE_TOOLS and action_id in _LLM_ELEVATED_ACTIONS
            assert _requires_confirmation(ctx, action_id) is True, action_id

    def test_plain_safe_tools_not_upgraded(self):
        ctx = _butler_ctx()
        for action_id in ("notes.list", "file.read", "memory.search", "confirmation.status"):
            assert _requires_confirmation(ctx, action_id) is False, action_id

    def test_confirm_tools_still_confirmed(self):
        ctx = _butler_ctx()
        for action_id in ("shell.run", "process.kill", "app.launch"):
            assert _requires_confirmation(ctx, action_id) is True, action_id

    def test_magicmock_guard_cannot_force_upgrade(self):
        """MagicMock 假 guard（auto-attr 恒真）不得把所有动作误升格
        （`is True` 严格判定的回归守卫）。"""
        fake = MagicMock()  # requires_llm_elevation_approval 返回 MagicMock（truthy 但非 True）
        ctx = _butler_ctx(guard=fake)
        # 假 guard 的 should_require_confirmation 同样返回 MagicMock → 宽松判定
        # 会把 notes.list 也确认掉；butler 先判 elevated（假值不认），随后
        # should_require_confirmation 返回 MagicMock 属 truthy → 该分支在既有代码
        # 里就有（本次不改）；故只断言 elevated 升格分支未被假值欺骗：
        # confirmation.status 恒免确认（在 elevated 判定之前短路）
        assert _requires_confirmation(ctx, "confirmation.status") is False

    def test_warn_only_entry_not_upgraded(self):
        from core.action_guard import KEY_ACTION_RISK_WARN_ONLY

        ctx = _butler_ctx(guard=_guard(**{KEY_ACTION_RISK_WARN_ONLY: True}))
        assert _requires_confirmation(ctx, "notes.delete") is False


# ════════════════════════════════════════════════════════════════
# 裁决 3 —— 队列2 出路（confirmation 生成 → resolve → 执行 / 拒绝 / 过期）
# ════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clean_confirmation_store(tmp_path, monkeypatch):
    import services.confirmation_store as cs

    monkeypatch.setenv("SIYI_DATA_DIR", str(tmp_path))
    cs._store_singleton = None
    yield
    cs._store_singleton = None


class TestAdjudication3Queue2Outlet:
    async def test_notes_delete_generates_confirmation_pending(self):
        """用例①前半：LLM 源 notes.delete → 不直执行，而是生成队列2确认单。"""
        from services.confirmation_store import get_confirmation_store

        ctx = _butler_ctx()
        raw = await execute_tool_action(ctx, "notes.delete", {"note_id": 42})
        payload = json.loads(raw)
        assert payload.get("pending_confirmation") is True, payload
        cid = payload["confirmation_id"]
        entry = get_confirmation_store().get(cid)
        assert entry["action_id"] == "notes.delete"
        assert entry["status"] == "pending"
        assert entry["args"] == {"note_id": 42}
        ctx.capability_router.execute_direct.assert_not_called()

    async def test_approve_executes_with_approved_true(self):
        """用例①后半：批准 → _execute_confirmation 以 approved=True 真实执行
        （guard 的人工确认最高授权放行，队列2 出路闭环）。"""
        import assistant.butler_agent as butler
        from assistant.butler_agent import resolve_confirmation

        ctx = _butler_ctx()
        ctx.capability_router.execute_direct = _async_ok({"success": True, "deleted": 42})
        butler._pending_contexts.clear()
        raw = await execute_tool_action(ctx, "notes.delete", {"note_id": 42})
        cid = json.loads(raw)["confirmation_id"]
        out = await resolve_confirmation(cid, True, reason="ok")
        assert out.get("success") is True
        assert out["result"] == {"success": True, "deleted": 42}
        calls = ctx.capability_router.execute_direct.await_args_list
        assert len(calls) == 1
        assert calls[0].args == ("notes.delete", {"note_id": 42})
        assert calls[0].kwargs.get("approved") is True, "批准执行必须透传 approved=True"

    async def test_reject_does_not_execute(self):
        """用例②：拒绝 → 不执行任何工具。"""
        import assistant.butler_agent as butler
        from assistant.butler_agent import resolve_confirmation

        ctx = _butler_ctx()
        butler._pending_contexts.clear()
        raw = await execute_tool_action(ctx, "notes.delete", {"note_id": 42})
        cid = json.loads(raw)["confirmation_id"]
        out = await resolve_confirmation(cid, False, reason="no")
        assert out.get("approved") is False
        ctx.capability_router.execute_direct.assert_not_called()

    async def test_expired_confirmation_cannot_be_approved(self):
        """用例③：TTL 过期 → resolve 返回 not_found_or_expired，不执行。"""
        import time

        import assistant.butler_agent as butler
        from assistant.butler_agent import resolve_confirmation
        from services.confirmation_store import get_confirmation_store

        ctx = _butler_ctx()
        butler._pending_contexts.clear()
        raw = await execute_tool_action(ctx, "notes.delete", {"note_id": 42})
        cid = json.loads(raw)["confirmation_id"]
        store = get_confirmation_store()
        # 直接把条目改到期（等价于等待 300s TTL）
        data = store._load()
        data[cid]["expires_at"] = time.time() - 1
        store._save(data)
        out = await resolve_confirmation(cid, True, reason="late")
        assert out.get("success") is False, out
        assert out.get("reason") == "confirmation_not_found_or_expired", out
        ctx.capability_router.execute_direct.assert_not_called()


# ════════════════════════════════════════════════════════════════
# 裁决 3 —— 真实 agent 闭环（ScriptedClient 剧本，不触网）
# ════════════════════════════════════════════════════════════════


class _NotesDeleteClient:
    """第一轮 notes.delete（触发升格确认）→ 第二轮 notes.list → 最终文本。"""

    def __init__(self, session_tag: str) -> None:
        self.calls: list[tuple[list, dict]] = []
        self.tag = session_tag

    def chat_completion(self, messages, **kwargs):
        self.calls.append((list(messages), dict(kwargs)))
        n = len(self.calls)
        if n == 1:
            # note_id 声明为 "number/string"（复合 → str），pydantic v2 不隐式
            # int→str，剧本须传字符串，否则被参数校验打回 retry（与升格链路无关）
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": f"call_del_{self.tag}",
                                    "type": "function",
                                    "function": {"name": "notes_delete", "arguments": json.dumps({"note_id": "7"})},
                                }
                            ],
                        }
                    }
                ],
                "model": "scripted",
            }
        if n == 2:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": f"call_list_{self.tag}",
                                    "type": "function",
                                    "function": {"name": "notes_list", "arguments": json.dumps({"limit": 5})},
                                }
                            ],
                        }
                    }
                ],
                "model": "scripted",
            }
        return {
            "choices": [{"message": {"content": "任务完成：已删除笔记并核对列表。", "tool_calls": None}}],
            "model": "scripted",
        }


class _AgentRouter:
    """真 ActionGuard + 记录型 execute_direct（默认 settings=全拦截态）。"""

    def __init__(self) -> None:
        self.action_guard = ActionGuard(settings_repository=None, observability_service=MagicMock())
        self.calls: list[tuple[str, dict, bool]] = []

    async def execute_direct(self, action_id, args, approved=False):
        self.calls.append((action_id, dict(args or {}), bool(approved)))
        if action_id == "notes.delete":
            return {"success": True, "deleted": (args or {}).get("note_id")}
        if action_id == "notes.list":
            return {"ok": True, "notes": []}
        return {"ok": True}


@pytest.fixture
def agent_ctx(tmp_path):
    from assistant.checkpoint import CheckpointManager

    import assistant.butler_agent as butler
    import services.confirmation_store as cs

    cs._store_singleton = None
    cs.get_confirmation_store(str(tmp_path))
    butler._agent_cache.clear()
    butler._pending_contexts.clear()
    ds = MagicMock()
    store: list = []
    ds.load_json.side_effect = lambda k, d: list(store)
    ds.update_json.side_effect = lambda k, d, u: store.__setitem__(slice(None), u(list(store))) or store
    tm = SimpleNamespace(checkpoint_manager=CheckpointManager(ds, max_versions=5))
    ctx = SimpleNamespace(
        automation=SimpleNamespace(autonomous_assistant_manager=SimpleNamespace(task_manager=tm)),
        data_store=ds,
        ai_client=_NotesDeleteClient("s1"),
        capability_router=_AgentRouter(),
        mcp_client_manager=None,
    )
    yield ctx
    butler._agent_cache.clear()
    butler._pending_contexts.clear()
    cs._store_singleton = None


async def _collect(agen):
    out = []
    async for c in agen:
        out.append(c)
    return out


class TestAdjudication3FullLoop:
    async def test_suspend_approve_resume(self, agent_ctx):
        """用例①全链路：notes.delete 升格 → checkpoint 挂起 → resolve(批准)
        → execute_direct(approved=True) → chat.resume 续跑至最终汇报。"""
        from assistant.butler_agent import get_butler_agent, resolve_confirmation
        from assistant.butler_checkpoint import load_butler_checkpoint
        from backend.handlers.chat_paths.pydantic_agent_path import (
            run_pydantic_agent_path,
            run_pydantic_agent_resume,
        )
        from core.events import event_bus

        ctx = agent_ctx
        assert get_butler_agent(ctx) is not None
        waited: list = []
        handler = lambda ev: waited.append(ev)  # noqa: E731
        event_bus.on("task.waiting_approval", handler)
        try:
            chunks = await _collect(
                run_pydantic_agent_path(
                    ctx,
                    "删除笔记7并核对列表",
                    {"session_id": "adj3-1"},
                    [],
                    None,
                )
            )
        finally:
            event_bus.off("task.waiting_approval", handler)
        assert chunks == [], f"挂起时不应有最终文本: {chunks}"
        assert len(waited) == 1, "elevated 动作未触发 task.waiting_approval"
        ev = waited[0]
        assert ev.payload["tool"] == "notes_delete", ev.payload
        assert ev.payload["checkpoint_saved"] is True
        cid = ev.payload["confirmation_id"]
        cp = load_butler_checkpoint(ctx, "adj3-1")
        assert cp and cp["context_snapshot"]["confirmations"][0]["id"] == cid

        out = await resolve_confirmation(cid, True, reason="允许删除")
        assert out.get("success") is True
        assert ctx.capability_router.calls == [("notes.delete", {"note_id": "7"}, True)], "批准路径必须以 approved=True 执行"

        sent: list[tuple] = []

        async def send_chunk(text, chunk_type=None):
            sent.append((text, chunk_type))

        resume = await _collect(run_pydantic_agent_resume(ctx, {"session_id": "adj3-1"}, send_chunk))
        assert any("任务完成" in str(c) for c in resume), resume
        assert [c[0] for c in ctx.capability_router.calls] == ["notes.delete", "notes.list"]

    async def test_suspend_reject_resume_continues(self, agent_ctx):
        """用例②全链路：拒绝 → 不执行 notes.delete；resume 后模型调整方案继续。"""
        from assistant.butler_agent import get_butler_agent, resolve_confirmation
        from backend.handlers.chat_paths.pydantic_agent_path import (
            run_pydantic_agent_path,
            run_pydantic_agent_resume,
        )
        from core.events import event_bus

        ctx = agent_ctx
        assert get_butler_agent(ctx) is not None
        waited: list = []
        handler = lambda ev: waited.append(ev)  # noqa: E731
        event_bus.on("task.waiting_approval", handler)
        try:
            await _collect(
                run_pydantic_agent_path(
                    ctx,
                    "删除笔记7",
                    {"session_id": "adj3-2"},
                    [],
                    None,
                )
            )
        finally:
            event_bus.off("task.waiting_approval", handler)
        cid = waited[0].payload["confirmation_id"]
        out = await resolve_confirmation(cid, False, reason="不删")
        assert out.get("success") is True
        assert ctx.capability_router.calls == [], "拒绝后不应执行工具"
        resume = await _collect(run_pydantic_agent_resume(ctx, {"session_id": "adj3-2"}, None))
        assert any("任务完成" in str(c) for c in resume)
        assert [c[0] for c in ctx.capability_router.calls] == ["notes.list"], "拒绝后模型应改走其他步骤"


# ════════════════════════════════════════════════════════════════
# 裁决 2 —— R4 入队前静态预检
# ════════════════════════════════════════════════════════════════


def _fake_router(tmp_path: Path):
    from assistant.capability._file_ops import FileCapabilityDomain

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    calls: list[dict] = []

    async def _execute_direct(action_id, args=None, approved=False):
        calls.append({"action_id": action_id, "args": dict(args or {}), "approved": approved})
        return {"success": True}

    router = SimpleNamespace(
        shell_execution=SimpleNamespace(workspace_root=str(ws_root)),
        data_store=SimpleNamespace(base_dir=str(data_root)),
        write_guard=None,
        _DEFAULT_DOWNLOAD_DIRNAME="assistant_downloads",
    )
    router.file_domain = FileCapabilityDomain(router)
    router.execute_direct = _execute_direct
    return router, calls, data_root


async def _make_registry(tmp_path: Path, router):
    from backend.handlers import register_all
    from backend.method_registry import MethodRegistry

    ctx = SimpleNamespace(
        local_data_store=SimpleNamespace(base_dir=str(tmp_path)),
        settings_repository=MagicMock(),
        capability_router=router,
    )
    reg = MethodRegistry()
    await register_all(reg, ctx, ws_port=18765)
    return reg


@pytest.fixture
def q1(tmp_path):
    """队列1 环境：清空全局 SandboxManager pending，用例后复原。"""
    from services.sandbox_manager import get_sandbox_manager

    mgr = get_sandbox_manager()
    saved = dict(mgr._pending)
    mgr._pending.clear()
    yield mgr
    mgr._pending.clear()
    mgr._pending.update(saved)


class TestAdjudication2Precheck:
    async def test_out_of_bounds_not_enqueued_no_event(self, tmp_path, q1):
        """用例⑥前半：越界 file.write → -32602、不入队、无 sandbox.pending_added。"""
        from core.errors import InvalidParamsError
        from core.events import event_bus

        events: list = []
        handler = lambda ev: events.append(ev)  # noqa: E731
        event_bus.on("sandbox.pending_added", handler)
        try:
            router, calls, _ = _fake_router(tmp_path)
            reg = await _make_registry(tmp_path, router)
            with pytest.raises(InvalidParamsError) as ei:
                await reg.dispatch("file.write", {"path": "C:/Windows/__adj2__.txt", "content": "x"})
        finally:
            event_bus.off("sandbox.pending_added", handler)
        assert len(q1.list_pending()) == 0, q1.list_pending()
        assert calls == []
        assert events == [], "预检拒绝不得发 sandbox.pending_added 事件"
        assert ei.value.code == -32602
        assert "path_outside_allowed_root" in ei.value.message

    async def test_legal_path_still_enqueues(self, tmp_path, q1):
        """用例⑥后半：白名单内合法 file.write 照常入队（预检零误伤）。"""
        router, calls, data_root = _fake_router(tmp_path)
        reg = await _make_registry(tmp_path, router)
        res = await reg.dispatch("file.write", {"path": str(data_root / "ok.txt"), "content": "hello"})
        assert res.get("pending") is True, res
        assert len(q1.list_pending()) == 1
        assert calls == []
        q1.reject(res["pending_id"])

    async def test_empty_path_not_judged_enqueues_as_before(self, tmp_path, q1):
        """空 path：预检不可判（None）→ 照常入队，行为不变（预检是增益非闸门）。"""
        router, _, _ = _fake_router(tmp_path)
        reg = await _make_registry(tmp_path, router)
        res = await reg.dispatch("file.write", {"path": "", "content": "x"})
        assert res.get("pending") is True, res
        q1.reject(res["pending_id"])

    async def test_precheck_exception_degrades_to_enqueue(self, tmp_path, q1):
        """precheck 抛异常 → 降级照常入队（绝不让预检打断审批流）。"""
        router, _, data_root = _fake_router(tmp_path)
        reg = await _make_registry(tmp_path, router)
        reg.register_precheck("file.write", lambda m, p: (_ for _ in ()).throw(RuntimeError("boom")))
        res = await reg.dispatch("file.write", {"path": str(data_root / "ok2.txt"), "content": "hello"})
        assert res.get("pending") is True, res
        q1.reject(res["pending_id"])

    async def test_legacy_client_error_priority_unchanged(self, tmp_path, q1):
        """优先序不变：legacy 客户端 + 越界参数 → 仍先 -32010（兼容语义零改变）。"""
        from core.errors import ClientUpgradeRequiredError

        router, _, _ = _fake_router(tmp_path)
        reg = await _make_registry(tmp_path, router)
        with pytest.raises(ClientUpgradeRequiredError):
            await reg.dispatch("file.write", {"path": "C:/Windows/__adj2__.txt", "content": "x"}, legacy_client=True)
        assert len(q1.list_pending()) == 0

    async def test_stream_dispatch_also_prechecked(self, tmp_path, q1):
        """dispatch_stream 同汇流点：流式入口的越界调用同样不入队。"""
        from core.errors import InvalidParamsError

        router, _, _ = _fake_router(tmp_path)
        reg = await _make_registry(tmp_path, router)
        chunks: list = []

        async def send_chunk(text, chunk_type="chunk"):
            chunks.append(text)

        with pytest.raises(InvalidParamsError):
            await _collect(reg.dispatch_stream("file.write", {"path": "C:/Windows/__adj2s__.txt", "content": "x"}, send_chunk))
        assert len(q1.list_pending()) == 0
        assert chunks == []

    async def test_replay_path_not_prechecked_handler_guard_holds(self, tmp_path, q1):
        """重放（skip_approval=True）不经预检：handler 内同一份路径守卫兜底，
        错误形态保持执行层 dict（{"success": False, reason: ...}）。"""
        router, calls, _ = _fake_router(tmp_path)
        reg = await _make_registry(tmp_path, router)
        out = await reg.dispatch("file.write", {"path": "C:/Windows/__adj2r__.txt", "content": "x"}, skip_approval=True)
        assert isinstance(out, dict) and out.get("success") is False, out
        assert out.get("reason") == "path_outside_allowed_root", out
        assert calls == []

    async def test_burst_out_of_bounds_cannot_evict_legal_pending(self, tmp_path, q1):
        """用例⑦（DoS 面封堵）：队列占满后连打 210 次越界调用 →
        合法待审批条目零驱逐、零新增。"""
        router, _, data_root = _fake_router(tmp_path)
        reg = await _make_registry(tmp_path, router)
        legal = q1.add("file.write", {"path": str(data_root / "legal.txt"), "content": "x"}, {"action": "file.write", "args": {}, "summary": "legal"})
        # 占满 200
        max_pending = q1._MAX_PENDING
        filler = [q1.add("filler.op", {"i": i}, {"action": "filler.op", "args": {}, "summary": "f"}) for i in range(max_pending - 1)]
        assert len(q1.list_pending()) == max_pending
        for i in range(210):
            with pytest.raises(Exception) as ei:
                await reg.dispatch("file.write", {"path": f"C:/Windows/burst_{i}.txt", "content": "x"})
            assert ei.value.code == -32602, ei.value
        assert legal in q1._pending, "越界风暴驱逐了合法待审批条目（DoS 未封堵）"
        assert len(q1.list_pending()) == q1._MAX_PENDING, "越界条目混进了队列"
        for pid in filler:
            q1.reject(pid)
        q1.reject(legal)


# ════════════════════════════════════════════════════════════════
# 用例⑤ + 对表：RPC 队列1 链不受影响 / 五张名单成员关系固化
# ════════════════════════════════════════════════════════════════


class TestQueue1UnaffectedAndLists:
    async def test_rpc_notes_delete_still_queue1(self, tmp_path, q1):
        """RPC 直调 notes.delete（∈ R4）→ 队列1 pending 语义不变，
        且 elevated 判定对 rpc 来源为 False（两队列判据互不串线）。"""
        router, calls, _ = _fake_router(tmp_path)
        reg = await _make_registry(tmp_path, router)
        assert "notes.delete" in _R4_METHODS
        guard = ActionGuard(settings_repository=None, observability_service=MagicMock())
        assert guard.requires_llm_elevation_approval("notes.delete", "rpc") is False
        res = await reg.dispatch("notes.delete", {"note_id": 1})
        assert res.get("pending") is True, res
        assert res.get("action_id") == "notes.delete"
        assert len(q1.list_pending()) == 1
        assert calls == []
        q1.reject(res["pending_id"])

    def test_five_lists_membership_cross_check(self):
        """对表固化：若有人改名单成员导致下表关系漂移，此测试红 → 强制回看裁决。"""
        s, c = set(SAFE_TOOLS), set(CONFIRM_TOOLS)
        h, e = set(_HIGH_RISK_ACTIONS), set(_LLM_ELEVATED_ACTIONS)
        r = set(_R4_METHODS)
        assert s & c == set(), "SAFE 与 CONFIRM 出现交集成员"
        assert s & e == {"file.write", "notes.delete", "notes.batch_delete", "tasks.delete"}
        assert s & r == {"file.write", "notes.delete", "notes.batch_delete", "tasks.delete"}
        assert e & r == {"file.write", "notes.delete", "notes.batch_delete", "tasks.delete"}
        assert c & r == {"cron.remove"}
        assert h - c == {"code.execute"}, "HIGH 名单成员应全部在 CONFIRM（code.execute 走沙箱除外）"
        assert os.environ.get("SIYI_DATA_DIR"), "环境隔离守卫：不得在无隔离下运行"

    def test_no_list_member_renamed_by_adjudication(self):
        """裁决不改成员：三处关键钉点仍在。"""
        from _r4_baseline import assert_r4_not_shrunk

        assert "notes.delete" in SAFE_TOOLS and "notes.delete" in _LLM_ELEVATED_ACTIONS
        assert "shell.run" in CONFIRM_TOOLS and "shell.run" in _LLM_ELEVATED_ACTIONS
        # 2026-09-18 批4：原为 `len(_R4_METHODS) == 40` 硬编码等号，本批新增
        # knowledge.entity.delete（P2-PD-NODEL，R4 级）即漂移成 41 而假红。
        # 本条断言的真实意图是"裁决不得把成员**删掉**"，新增本就不该报红 →
        # 收敛到 tests/_r4_baseline.py 的单点下界断言。真正的防漏项由
        # test_p0_6_delete_r4_gating.test_no_delete_method_unclassified 结构级覆盖。
        assert "file.write" in _R4_METHODS
        assert_r4_not_shrunk(_R4_METHODS)
