"""审批闭环端到端验证（V6c / A4 补足）。

内核发起 DANGER 审批 → ConfirmationStore 落条目 → confirmation.* RPC 可见
→ 前端批准（resolve）→ status 读到 approved → 内核可据此续跑。

三段覆盖：
- ConfirmationStore 闭环：create(pending) → list 可见 → resolve(approve)
  → status approved → resolve 一次性（二次返回 None）；reject / expired 分支。
- 网关路径 confirm/tool 事件：FakeGateway emit confirm/tool AgentRunEvent →
  run_gateway_path 逐事件映射为 chunk_type="confirm"/"tool" chunk。
- confirmation.* RPC 契约：handle_confirmation_list/status/resolve 经 store 工作。

隔离：纯内存/临时目录，不依赖真实内核与后端进程。
"""

from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from backend.handlers.chat_paths import run_gateway_path
from backend.handlers._confirmations import (
    handle_confirmation_list,
    handle_confirmation_resolve,
    handle_confirmation_status,
)
from services.agent_gateway.adapters import _parse_codex_events
from services.agent_gateway.protocol import AgentRunEvent
from services.confirmation_store import ConfirmationStore, get_confirmation_store


@pytest.fixture
def store(tmp_path) -> ConfirmationStore:
    """隔离 ConfirmationStore（临时数据目录）。"""
    s = ConfirmationStore(data_dir=str(tmp_path))
    s.clear()
    return s


# ── A. ConfirmationStore 闭环 ─────────────────────────────────


class TestStoreClosedLoop:
    def test_approve_loop(self, store: ConfirmationStore) -> None:
        """内核请求 → 审批通过 → status approved（完整闭环）。"""
        cid = store.create("shell.run", {"cmd": "rm -rf /tmp/x"}, source="mcp:kernel", title="执行命令")
        assert store.get(cid)["status"] == "pending"
        assert [e["confirmation_id"] for e in store.list() if e["status"] == "pending"] == [cid]
        resolved = store.resolve(cid, approved=True)
        assert resolved is not None and resolved["status"] == "approved"
        assert store.get(cid)["status"] == "approved"
        # 一次性：二次 resolve 返回 None
        assert store.resolve(cid, approved=True) is None

    def test_reject_loop(self, store: ConfirmationStore) -> None:
        cid = store.create("shell.run", {"cmd": "x"}, source="mcp:kernel", title="t")
        store.resolve(cid, approved=False)
        assert store.get(cid)["status"] == "rejected"
        assert store.resolve(cid, approved=False) is None

    def test_expired(self, store: ConfirmationStore) -> None:
        cid = store.create("shell.run", {}, source="mcp:kernel", title="t", ttl_s=0.01)
        time.sleep(0.05)
        assert store.get(cid)["status"] == "expired"
        assert cid not in [e["confirmation_id"] for e in store.list() if e["status"] == "pending"]


# ── B. 网关路径 confirm/tool 事件映射 ─────────────────────────


class _Collector:
    def __init__(self) -> None:
        self.chunks: list[tuple[str, str | None]] = []

    async def __call__(self, text, chunk_type=None):
        self.chunks.append((text, chunk_type))


class _FakeGateway:
    def __init__(self, events) -> None:
        self._events = events
        self.name = "dsh"

    def submit_stream(self, prompt, timeout_s=None, profile=None):
        for ev in self._events:
            yield ev


def _ctx(gateway) -> types.SimpleNamespace:
    settings = types.SimpleNamespace(get_setting=lambda key, default=None: "gateway" if key == "chat_route" else default)
    return types.SimpleNamespace(agent_gateway=gateway, settings_repository=settings)


@pytest.mark.asyncio
async def test_gateway_path_emits_confirm_chunk() -> None:
    """内核 emit confirm 事件 → run_gateway_path 产出 chunk_type='confirm'。"""
    confirm_payload = {
        "confirmation_id": "abc123",
        "action_id": "shell.run",
        "title": "执行高风险命令",
        "detail": "rm -rf /tmp",
    }
    gw = _FakeGateway(
        [
            AgentRunEvent(kind="status", payload={"phase": "started"}),
            AgentRunEvent(kind="confirm", payload=confirm_payload),
            AgentRunEvent(kind="result", payload={"text": "已请求确认", "usage": None}),
        ]
    )
    collector = _Collector()
    handled = await run_gateway_path(_ctx(gw), "执行 rm", {"kernel": True}, collector)
    assert handled is True
    confirm_chunks = [c for c, t in collector.chunks if t == "confirm"]
    assert len(confirm_chunks) == 1
    parsed = json.loads(confirm_chunks[0])
    assert parsed["confirmation_id"] == "abc123"
    assert parsed["action_id"] == "shell.run"
    assert parsed["title"] == "执行高风险命令"


@pytest.mark.asyncio
async def test_gateway_path_emits_tool_chunk() -> None:
    """内核 emit tool 事件 → run_gateway_path 产出 chunk_type='tool'（结构化工具步骤）。"""
    gw = _FakeGateway(
        [
            AgentRunEvent(kind="tool", payload={"tool": "siyi_notes_search", "args": {"query": "内核"}}),
            AgentRunEvent(kind="result", payload={"text": "完成", "usage": None}),
        ]
    )
    collector = _Collector()
    handled = await run_gateway_path(_ctx(gw), "搜索笔记", {"kernel": True}, collector)
    assert handled is True
    tool_chunks = [c for c, t in collector.chunks if t == "tool"]
    assert len(tool_chunks) == 1
    assert json.loads(tool_chunks[0])["tool"] == "siyi_notes_search"


# ── C. confirmation.* RPC 契约 ────────────────────────────────


@pytest.mark.asyncio
async def test_confirmation_rpc_closed_loop(tmp_path) -> None:
    """list → resolve → status 三段 RPC 经 store 闭环。

    handler 走进程内单例 get_confirmation_store()，故先把它指向 tmp_path
    再建条目，保证 handler 与测试读到同一存储。

    H 项：批准操作必须带理由（前端必填 + 后端校验），理由写入审计（status 回显）。
    """
    store = get_confirmation_store(str(tmp_path))
    store.clear()
    cid = store.create("app.launch", {"path": "x"}, source="mcp:kernel", title="启动应用")
    listed = await handle_confirmation_list({})
    assert any(e["confirmation_id"] == cid for e in listed["confirmations"])

    await handle_confirmation_resolve({"confirmation_id": cid, "approved": True, "reason": "用户确认启动应用"})
    status = await handle_confirmation_status({"confirmation_id": cid})
    assert status["status"] == "approved"
    assert status["source"] == "mcp:kernel"
    assert status["reason"] == "用户确认启动应用"  # reason 写入审计并可回显

    # 已批准 → 再次 resolve 一次性（返回 error 包裹而非重复翻转）
    again = await handle_confirmation_resolve({"confirmation_id": cid, "approved": True, "reason": "x"})
    assert not isinstance(again, dict) or again.get("status") != "approved"


@pytest.mark.asyncio
async def test_confirmation_resolve_requires_reason(tmp_path) -> None:
    """H 项：批准操作不带理由 → 后端拦截（不带理由被拦，条目保持 pending）。"""
    store = get_confirmation_store(str(tmp_path))
    store.clear()
    cid = store.create("app.launch", {"path": "x"}, source="mcp:kernel", title="启动应用")
    blocked = await handle_confirmation_resolve({"confirmation_id": cid, "approved": True})
    assert blocked.get("success") is False
    assert blocked.get("reason") == "approval_reason_required"
    status = await handle_confirmation_status({"confirmation_id": cid})
    assert status["status"] == "pending"  # 未批准
    # 拒绝操作不强制理由（缺省空串，向后兼容）
    ok = await handle_confirmation_resolve({"confirmation_id": cid, "approved": False})
    assert ok.get("success") is True
    st = await handle_confirmation_status({"confirmation_id": cid})
    assert st["status"] == "rejected"


@pytest.mark.asyncio
async def test_confirmation_status_not_found(store: ConfirmationStore) -> None:
    st = await handle_confirmation_status({"confirmation_id": "ghost"})
    assert st["status"] == "expired_or_not_found"


def test_singleton_isolated_by_data_dir(tmp_path) -> None:
    """get_confirmation_store 按 data_dir 隔离（避免测试间串扰）。"""
    a = get_confirmation_store(str(tmp_path / "a"))
    b = get_confirmation_store(str(tmp_path / "b"))
    assert a is not b
    assert a.data_dir != b.data_dir


# ── D. Codex JSONL 工具事件解析（tool_events 来源之一）─────────

_SAMPLE_CODEX_JSONL = (
    '{"type":"item","payload":{"type":"text","text":"让我查一下待办"}}\n'
    '{"type":"item","payload":{"type":"shell_tool_call","tool":"shell","command":"siyi_tasks_list","status":"success"}}\n'
    '{"type":"message","payload":{"role":"assistant","content":[{"type":"text","text":"目前有 2 条待办"}]}}\n'
)


class TestCodexToolEvents:
    def test_parses_tool_and_result(self) -> None:
        events = _parse_codex_events(_SAMPLE_CODEX_JSONL)
        kinds = [e.kind for e in events]
        assert "tool" in kinds
        assert "result" in kinds
        assert "delta" in kinds
        tool_ev = next(e for e in events if e.kind == "tool")
        assert tool_ev.payload["tool"] == "shell"
        assert tool_ev.payload["command"] == "siyi_tasks_list"
        result_ev = next(e for e in events if e.kind == "result")
        assert result_ev.payload["text"] == "目前有 2 条待办"

    def test_ignores_garbage_lines(self) -> None:
        events = _parse_codex_events('not json\n{"type":"message","payload":{"content":[{"type":"text","text":"ok"}]}}\n')
        assert [e.kind for e in events] == ["result"]
        assert events[0].payload["text"] == "ok"

    def test_no_result_when_only_tool(self) -> None:
        events = _parse_codex_events('{"type":"item","payload":{"type":"shell_tool_call","tool":"shell","command":"ls"}}\n')
        assert [e.kind for e in events] == ["tool"]
        assert events[0].payload["command"] == "ls"


# ── E. ConfirmationWatcher：跨进程审批补发 ─────────────────────
# 根因：MCP 内核经 siyi_confirm_request 只写共享 confirmations.json、不发射事件，
# 前端收不到 action.confirm_required。watcher 在后端轮询并补发。


class _FakeEventBus:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:
        self.events.append(event)


def _make_watcher(store: ConfirmationStore) -> tuple:
    from backend.confirmation_watcher import ConfirmationWatcher

    bus = _FakeEventBus()
    return ConfirmationWatcher(store, bus), bus


def test_watcher_emits_kernel_pending_once(store: ConfirmationStore) -> None:
    """mcp:kernel pending 条目 → 补发 action.confirm_required，且去重不重发。"""
    cid = store.create("notes.delete", {"note_id": "_daily_2026-06-06"}, source="mcp:kernel", title="删除乱码测试笔记", detail="d", ttl_s=300)
    watcher, bus = _make_watcher(store)
    watcher._scan()
    assert len(bus.events) == 1
    ev = bus.events[0]
    assert ev.event_type == "action.confirm_required"
    assert ev.payload["confirmation_id"] == cid
    assert ev.payload["action_id"] == "notes.delete"
    assert ev.payload["args"] == {"note_id": "_daily_2026-06-06"}
    # 二次扫描不重发（内存去重）
    watcher._scan()
    assert len(bus.events) == 1


def test_watcher_skips_butler_and_resolved(store: ConfirmationStore) -> None:
    """butler 来源（自身已 emit）与已决/过期条目不补发。"""
    butler_cid = store.create("confirmation.status", {}, source="butler", ttl_s=300)
    approved_cid = store.create("app.launch", {}, source="mcp:kernel", ttl_s=300)
    store.resolve(approved_cid, approved=True)
    expired_cid = store.create("shell.run", {}, source="mcp:kernel", ttl_s=0.01)
    import time

    time.sleep(0.05)
    watcher, bus = _make_watcher(store)
    watcher._scan()
    assert len(bus.events) == 0
    assert all(ev.payload["confirmation_id"] not in (butler_cid, approved_cid, expired_cid) for ev in bus.events)


# ── C. DSH shell.run 两阶段确认（方案 A，2026-09-09）─────────────


class TestTwoPhaseShellConfirmation:
    """批准→真实执行→结果落store→重发复用，闭环。"""

    class _ShellDom:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def shell_run(self, args: dict) -> dict:
            self.calls.append(args)
            return {"output": f"ok:{args.get('command', '')}"}

    def _ctx(self, dom) -> types.SimpleNamespace:
        return types.SimpleNamespace(capability_router=types.SimpleNamespace(shell_domain=dom))

    def test_request_then_approve_executes_and_reuses(self, monkeypatch) -> None:
        import services.confirmation_flow as cf
        from services.confirmation_flow import (
            consume_approved_shell,
            request_shell_confirmation,
        )

        # 隔离 store（临时目录）——monkeypatch 单例数据目录
        import tempfile

        tmp = tempfile.mkdtemp()
        from services.confirmation_store import ConfirmationStore

        st = ConfirmationStore(data_dir=tmp)
        st.clear()
        monkeypatch.setattr(cf, "get_confirmation_store", lambda: st)
        # 重置模块级状态，保证幂等注册与消费集合独立
        monkeypatch.setattr(cf, "_shell_consumed", set())

        dom = self._ShellDom()
        ctx = self._ctx(dom)
        args = {"command": "dir", "timeout": 5}

        # ① 发起确认 → 返回 pending
        out = request_shell_confirmation(ctx, args)
        assert out["pending_confirmation"] is True
        cid = out["confirmation_id"]
        assert st.get(cid)["status"] == "pending"

        # ② 批准（模拟 confirmation.resolve → resolve_confirmation 委托执行 handler）
        import asyncio

        resolved = asyncio.run(cf.resolve_confirmation(cid, True, "确认"))
        assert resolved["success"] is True
        # 批准时真正执行了 shell.run，结果已落入 store
        assert dom.calls == [args]
        entry = st.get(cid)
        assert entry["status"] == "approved"
        assert entry["result"]["output"] == "ok:dir"

        # ③ 重发：consume_approved_shell 直接复用结果，不再二次确认
        reused = consume_approved_shell(args)
        assert reused is not None and reused["output"] == "ok:dir"
        # 一次性：再次消费同命令返回 None（避免死循环）
        assert consume_approved_shell(args) is None

    def test_reject_does_not_execute(self, monkeypatch) -> None:
        import services.confirmation_flow as cf
        from services.confirmation_flow import request_shell_confirmation, consume_approved_shell
        import tempfile, asyncio
        from services.confirmation_store import ConfirmationStore

        st = ConfirmationStore(data_dir=tempfile.mkdtemp())
        st.clear()
        monkeypatch.setattr(cf, "get_confirmation_store", lambda: st)
        monkeypatch.setattr(cf, "_shell_consumed", set())

        dom = self._ShellDom()
        cid = request_shell_confirmation(self._ctx(dom), {"command": "whoami"})["confirmation_id"]
        asyncio.run(cf.resolve_confirmation(cid, False, "拒绝"))
        assert dom.calls == [], "拒绝不应执行命令"
        entry = st.get(cid)
        assert entry["status"] == "rejected"
        # 拒绝结果不供复用
        assert consume_approved_shell({"command": "whoami"}) is None
