"""P0 修复回归：chat_paths/_exec_step.py 缺失模块补齐（2026-08-31）。

修复前：pydantic_agent_path / smolagents_path 在 chat 管家/agent 路径运行到
对应函数时 import backend.handlers.chat_paths._exec_step 必然 ImportError。
"""

import importlib


def test_module_importable():
    """模块存在且可导入（修复前 ImportError）。"""
    mod = importlib.import_module("backend.handlers.chat_paths._exec_step")
    assert callable(mod.emit_exec_step)
    assert callable(mod.make_task_id)


def test_make_task_id_shape():
    from backend.handlers.chat_paths._exec_step import make_task_id

    tid = make_task_id("session-1", prefix="chat")
    assert isinstance(tid, str) and tid.startswith("chat-")
    assert len(tid) > len("chat-")
    # 每次调用唯一（INSERT OR REPLACE 语义要求 task_id 不碰撞）
    assert make_task_id("session-1", prefix="chat") != tid


def test_emit_exec_step_emits_on_bus(monkeypatch):
    """emit_exec_step 应以 payload 发射 exec.step 事件（TraceRecorder 订阅写库）。"""
    from core import events as core_events
    from backend.handlers.chat_paths._exec_step import emit_exec_step

    captured = {}

    def fake_emit(app_event):
        captured["event_type"] = getattr(app_event, "event_type", None)
        captured["payload"] = getattr(app_event, "payload", None)
        captured["source"] = getattr(app_event, "source", None)

    monkeypatch.setattr(core_events.event_bus, "emit", fake_emit)

    payload = {"task_id": "chat-abc", "step_type": "tool_call", "tool": "t", "summary": "s"}
    emit_exec_step(payload)
    assert captured.get("event_type") == "exec.step"
    assert captured.get("payload") == payload
    assert captured.get("source") == "chat_paths"


def test_emit_exec_step_silent_on_bus_failure(monkeypatch):
    """EventBus 异常时静默降级，绝不抛错打断 chat 流。"""
    from core import events as core_events
    from backend.handlers.chat_paths._exec_step import emit_exec_step

    def boom(_app_event):
        raise RuntimeError("bus down")

    monkeypatch.setattr(core_events.event_bus, "emit", boom)
    # 不应抛异常
    emit_exec_step({"task_id": "chat-1", "status": "started"})


def test_emit_exec_step_none_payload_noop():
    from backend.handlers.chat_paths._exec_step import emit_exec_step

    emit_exec_step(None)  # 不抛
    emit_exec_step({})  # 不抛
