"""P0-11 回归：ExecutionEngine / ExecutionRouter

验证：
1. 协议合规：实例必须满足 ExecutionEngine 才能注册（runtime_checkable）。
2. 优先级排序：低 priority 先尝试。
3. ``can_handle`` 返回 False 时跳过；抛异常时不中断后续引擎。
4. ``execute`` 返回 None 让下家继续；抛异常时被吞掉。
5. ReActEngineAdapter：non-EXECUTE / non-None intent 不参与；engine 为 None 直接 skip。
6. ComputerUseEngineAdapter：仅在 intent==EXECUTE 且文本含关键字时承接；
   ai_client=None 时返回 not_handled。
7. GoalRunEngineAdapter：把 ctx 字段映射成 run_goal 关键字参数；
   附带 classification.to_dict() 时回写到结果。
8. unregister 能按名字摘除引擎。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from assistant.dispatch_result import DispatchResult  # noqa: E402
from assistant.execution_router import (  # noqa: E402
    ComputerUseEngineAdapter,
    DispatchContext,
    ExecutionEngine,
    ExecutionRouter,
    GoalRunEngineAdapter,
    ReActEngineAdapter,
)
from assistant.intent_router import INTENT_CHAT, INTENT_EXECUTE  # noqa: E402


# ---------------- 小工具：可控的 fake engine ----------------

class _FakeEngine:
    def __init__(
        self,
        name: str,
        priority: int,
        *,
        accept: bool = True,
        result: DispatchResult | None = None,
        raise_can: bool = False,
        raise_exec: bool = False,
    ) -> None:
        self.name = name
        self.priority = priority
        self._accept = accept
        self._result = result
        self._raise_can = raise_can
        self._raise_exec = raise_exec
        self.calls_can: list[tuple] = []
        self.calls_exec: list[tuple] = []

    def can_handle(self, text, *, intent, ctx):
        self.calls_can.append((text, intent))
        if self._raise_can:
            raise RuntimeError(f"{self.name}.can_handle boom")
        return self._accept

    def execute(self, text, *, intent, ctx):
        self.calls_exec.append((text, intent))
        if self._raise_exec:
            raise RuntimeError(f"{self.name}.execute boom")
        return self._result


# ---------------- ExecutionRouter ----------------

def test_register_rejects_non_protocol_instances():
    router = ExecutionRouter()
    bogus = object()  # 既没有 name/priority，也没有 can_handle/execute
    with pytest.raises(TypeError):
        router.register(bogus)


def test_router_dispatch_respects_priority_order():
    """priority=0 应先于 priority=10 被尝试。"""
    router = ExecutionRouter()
    first_result = DispatchResult.handled(intent=INTENT_CHAT, data={"engine": "first"})
    e_low = _FakeEngine("low", priority=0, accept=True, result=first_result)
    e_high = _FakeEngine("high", priority=10, accept=True,
                         result=DispatchResult.handled(intent=INTENT_CHAT, data={"engine": "high"}))

    router.register(e_high)
    router.register(e_low)

    out = router.try_dispatch("hi", intent=None, ctx=DispatchContext())
    assert out is first_result
    assert e_low.calls_exec, "低优先级引擎必须先被调用"
    assert not e_high.calls_exec, "高优先级引擎在低优先级承接后不应再被调用"


def test_router_skips_engine_when_can_handle_false():
    router = ExecutionRouter()
    accepting_result = DispatchResult.handled(intent=INTENT_CHAT, data={"engine": "second"})
    e_no = _FakeEngine("skip", priority=0, accept=False)
    e_yes = _FakeEngine("take", priority=10, accept=True, result=accepting_result)
    router.register(e_no)
    router.register(e_yes)

    out = router.try_dispatch("hi", intent=None, ctx=DispatchContext())
    assert out is accepting_result
    assert e_no.calls_can and not e_no.calls_exec
    assert e_yes.calls_exec


def test_router_swallows_can_handle_exception_and_continues():
    router = ExecutionRouter()
    accepting_result = DispatchResult.handled(intent=INTENT_CHAT, data={"engine": "second"})
    e_boom = _FakeEngine("boom", priority=0, raise_can=True)
    e_ok = _FakeEngine("ok", priority=10, accept=True, result=accepting_result)
    router.register(e_boom)
    router.register(e_ok)

    out = router.try_dispatch("hi", intent=None, ctx=DispatchContext())
    assert out is accepting_result
    assert e_ok.calls_exec


def test_router_swallows_execute_exception_and_continues():
    router = ExecutionRouter()
    accepting_result = DispatchResult.handled(intent=INTENT_CHAT, data={"engine": "second"})
    e_boom = _FakeEngine("boom", priority=0, accept=True, raise_exec=True)
    e_ok = _FakeEngine("ok", priority=10, accept=True, result=accepting_result)
    router.register(e_boom)
    router.register(e_ok)

    out = router.try_dispatch("hi", intent=None, ctx=DispatchContext())
    assert out is accepting_result


def test_router_returns_none_when_no_engine_handles():
    router = ExecutionRouter()
    e1 = _FakeEngine("a", priority=0, accept=True, result=None)
    e2 = _FakeEngine("b", priority=10, accept=False)
    router.register(e1)
    router.register(e2)

    out = router.try_dispatch("hi", intent=None, ctx=DispatchContext())
    assert out is None


def test_router_unregister_removes_engine_by_name():
    router = ExecutionRouter()
    e = _FakeEngine("victim", priority=0, accept=True)
    router.register(e)
    assert any(x.name == "victim" for x in router.engines)
    assert router.unregister("victim") is True
    assert all(x.name != "victim" for x in router.engines)
    assert router.unregister("ghost") is False


# ---------------- ReActEngineAdapter ----------------

def test_react_adapter_skips_when_engine_none():
    adapter = ReActEngineAdapter(react_engine=None)
    assert adapter.can_handle("hi", intent=None, ctx=DispatchContext()) is False


def test_react_adapter_only_handles_none_or_execute_intent():
    re_engine = SimpleNamespace(process_message=lambda t: "tool_done")
    adapter = ReActEngineAdapter(re_engine)
    assert adapter.can_handle("hi", intent=None, ctx=DispatchContext()) is True
    assert adapter.can_handle("hi", intent=INTENT_EXECUTE, ctx=DispatchContext()) is True
    assert adapter.can_handle("hi", intent=INTENT_CHAT, ctx=DispatchContext()) is False


def test_react_adapter_passes_through_None_to_let_router_continue():
    re_engine = SimpleNamespace(process_message=lambda t: None)
    adapter = ReActEngineAdapter(re_engine)
    out = adapter.execute("hi", intent=None, ctx=DispatchContext())
    assert out is None


def test_react_adapter_wraps_reply_into_dispatch_result():
    re_engine = SimpleNamespace(process_message=lambda t: "tool ran ok")
    adapter = ReActEngineAdapter(re_engine)
    out = adapter.execute("帮我读文件", intent=None, ctx=DispatchContext())
    assert isinstance(out, DispatchResult)
    assert out.is_handled
    assert out.data["react_reply"] == "tool ran ok"
    assert out.data["classification"]["reason"] == "react_engine"


# ---------------- ComputerUseEngineAdapter ----------------

KEYWORDS = frozenset({"帮我登录", "帮我点击"})
TRIGGERS = frozenset({"在浏览器"})


def test_computer_use_adapter_ignores_non_execute_intent():
    adapter = ComputerUseEngineAdapter(
        computer_use=SimpleNamespace(),
        security=SimpleNamespace(),
        ai_client=SimpleNamespace(),
        keywords=KEYWORDS,
        triggers=TRIGGERS,
    )
    assert adapter.can_handle("帮我登录", intent=INTENT_CHAT, ctx=DispatchContext()) is False
    assert adapter.can_handle("帮我登录", intent=None, ctx=DispatchContext()) is False


def test_computer_use_adapter_keyword_match():
    adapter = ComputerUseEngineAdapter(
        computer_use=SimpleNamespace(),
        security=SimpleNamespace(),
        ai_client=SimpleNamespace(),
        keywords=KEYWORDS,
        triggers=TRIGGERS,
    )
    assert adapter.can_handle("帮我登录百度", intent=INTENT_EXECUTE, ctx=DispatchContext()) is True
    assert adapter.can_handle("在浏览器搜索", intent=INTENT_EXECUTE, ctx=DispatchContext()) is True
    assert adapter.can_handle("查询天气", intent=INTENT_EXECUTE, ctx=DispatchContext()) is False


def test_computer_use_adapter_returns_error_when_ai_client_missing():
    cu = SimpleNamespace(COMPUTER_USE_ACTIONS=("click",),
                         execute=lambda *a, **kw: pytest.fail("should not be called"))
    adapter = ComputerUseEngineAdapter(
        computer_use=cu,
        security=SimpleNamespace(),
        ai_client=None,
        keywords=KEYWORDS,
        triggers=TRIGGERS,
    )
    out = adapter.execute("帮我登录", intent=INTENT_EXECUTE, ctx=DispatchContext())
    assert out is not None
    assert out.is_handled is False
    assert "error" in out.data


def test_computer_use_adapter_invokes_engine_with_pre_approval():
    received: dict = {}

    def _exec(text, *, authorize_callback, cancellation_event):
        received["text"] = text
        received["has_callback"] = callable(authorize_callback)
        received["cancel"] = cancellation_event
        return {"ok": True, "actions_run": 2}

    cu = SimpleNamespace(COMPUTER_USE_ACTIONS=("click", "type"), execute=_exec)
    sec = SimpleNamespace(authorize_step=lambda step, source=None, pre_approved=None: True)
    cancel = threading.Event()
    ctx = DispatchContext(cancellation_event=cancel)

    adapter = ComputerUseEngineAdapter(
        computer_use=cu,
        security=sec,
        ai_client=object(),
        keywords=KEYWORDS,
        triggers=TRIGGERS,
    )
    out = adapter.execute("帮我登录百度", intent=INTENT_EXECUTE, ctx=ctx)
    assert out is not None and out.is_handled
    assert out.data["intent"] == INTENT_EXECUTE
    assert out.data["goal"] == "帮我登录百度"
    assert out.data["ok"] is True
    assert received["has_callback"]
    assert received["cancel"] is cancel


# ---------------- GoalRunEngineAdapter ----------------

def test_goal_run_adapter_can_handle_only_execute_intent():
    adapter = GoalRunEngineAdapter(run_goal=lambda *a, **kw: {})
    assert adapter.can_handle("x", intent=INTENT_EXECUTE, ctx=DispatchContext()) is True
    assert adapter.can_handle("x", intent=INTENT_CHAT, ctx=DispatchContext()) is False
    assert adapter.can_handle("x", intent=None, ctx=DispatchContext()) is False


def test_goal_run_adapter_maps_ctx_to_run_goal_kwargs():
    captured: dict = {}

    def _run_goal(goal, *, source, delivery_channel, chat_session_id, cancellation_event):
        captured.update(goal=goal, source=source, delivery_channel=delivery_channel,
                        chat_session_id=chat_session_id, cancellation_event=cancellation_event)
        return {"summary": "ok"}

    classification = SimpleNamespace(to_dict=lambda: {"intent": INTENT_EXECUTE, "reason": "kw"})
    ctx = DispatchContext(
        source="src", delivery_channel="inbox", chat_session_id="chat-7",
        cancellation_event=threading.Event(), classification=classification,
    )
    adapter = GoalRunEngineAdapter(run_goal=_run_goal)
    out = adapter.execute("看新闻", intent=INTENT_EXECUTE, ctx=ctx)

    assert captured["goal"] == "看新闻"
    assert captured["source"] == "src"
    assert captured["delivery_channel"] == "inbox"
    assert captured["chat_session_id"] == "chat-7"
    assert out is not None and out.is_handled
    assert out.data["intent"] == INTENT_EXECUTE
    assert out.data["classification"] == {"intent": INTENT_EXECUTE, "reason": "kw"}


def test_goal_run_adapter_handles_classification_without_to_dict():
    """ctx.classification 没有 to_dict 时不应抛错，但也不写 classification 字段。"""
    adapter = GoalRunEngineAdapter(run_goal=lambda goal, **kw: {"summary": "ok"})
    out = adapter.execute("hi", intent=INTENT_EXECUTE, ctx=DispatchContext())
    assert out is not None
    assert "classification" not in out.data


# ---------------- ExecutionEngine 协议 ----------------

def test_protocol_runtime_check_accepts_adapters():
    fake_react = ReActEngineAdapter(react_engine=None)
    assert isinstance(fake_react, ExecutionEngine)
    fake_goal = GoalRunEngineAdapter(run_goal=lambda *a, **kw: {})
    assert isinstance(fake_goal, ExecutionEngine)
    fake_cu = ComputerUseEngineAdapter(
        computer_use=SimpleNamespace(),
        security=SimpleNamespace(),
        ai_client=None,
        keywords=frozenset(),
        triggers=frozenset(),
    )
    assert isinstance(fake_cu, ExecutionEngine)
