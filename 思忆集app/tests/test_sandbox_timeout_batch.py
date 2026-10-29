# -*- coding: utf-8 -*-
"""P1-5：审批超时策略 + 批量审批测试。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.events import event_bus
from services.sandbox_manager import SandboxManager


class _EventCollector:
    def __init__(self, *event_types):
        self.types = event_types
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event):
        et = getattr(event, "event_type", "")
        if et in self.types:
            self.events.append((et, getattr(event, "payload", {}) or {}))


@pytest.fixture
def collector():
    c = _EventCollector("sandbox.timeout", "sandbox.timeout_warning")
    for t in c.types:
        event_bus.on(t, c)
    yield c
    for t in c.types:
        event_bus.off(t, c)


def test_timeout_rejects_expired_pending(monkeypatch, collector):
    monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_POLICY", "reject")
    mgr = SandboxManager()
    pid = mgr.add("file.write", {"path": "/tmp/x"}, {"diff": "..."})

    # 未过期：不处置
    assert mgr.check_timeouts() == []
    assert mgr.get(pid) is not None

    # 回拨创建时间制造过期
    mgr._pending[pid]["created_at"] = time.time() - 120
    processed = mgr.check_timeouts()

    assert len(processed) == 1
    assert processed[0]["status"] == "timed_out_rejected"
    assert mgr.get(pid) is None, "过期审批应被移除（默认拒绝）"
    timeouts = [p for t, p in collector.events if t == "sandbox.timeout"]
    assert len(timeouts) == 1
    assert timeouts[0]["pending_id"] == pid
    assert timeouts[0]["policy"] == "reject"


def test_timeout_dry_run_policy_downgrades(monkeypatch, collector):
    monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_POLICY", "dry_run")
    calls = []

    def executor(action_id, args):
        calls.append((action_id, dict(args)))
        return {"dry_run": True, "ok": True}

    mgr = SandboxManager()
    mgr.set_tool_executor(executor)
    pid = mgr.add("file.write", {"path": "/tmp/x"}, {})
    mgr._pending[pid]["created_at"] = time.time() - 120

    processed = mgr.check_timeouts()
    assert processed[0]["status"] == "timed_out_dryrun"
    assert calls and calls[0][1].get("dry_run") is True, "dry_run 策略应以只读预演降级"
    assert mgr.get(pid) is None


def test_timeout_warning_emitted_once(monkeypatch, collector):
    monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_SECONDS", "100")
    mgr = SandboxManager()
    pid = mgr.add("file.write", {}, {})
    # 剩余 50s（≤60s 提醒窗口）
    mgr._pending[pid]["created_at"] = time.time() - 50

    mgr.check_timeouts()
    mgr.check_timeouts()  # 第二次不应重复提醒

    warnings = [p for t, p in collector.events if t == "sandbox.timeout_warning"]
    assert len(warnings) == 1, "临期提醒每条目只发一次"
    assert warnings[0]["pending_id"] == pid
    assert 0 < warnings[0]["remaining_seconds"] <= 60
    assert mgr.get(pid) is not None, "临期未过期不应处置"


def test_timeout_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_SECONDS", "0")
    mgr = SandboxManager()
    pid = mgr.add("file.write", {}, {})
    mgr._pending[pid]["created_at"] = time.time() - 99999
    assert mgr.check_timeouts() == []
    assert mgr.get(pid) is not None


def test_batch_approve_executes_all(monkeypatch):
    monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_SECONDS", "0")
    executed = []
    mgr = SandboxManager()
    mgr.set_tool_executor(lambda action_id, args: executed.append(action_id) or {"ok": True})
    p1 = mgr.add("file.write", {"path": "/a"}, {})
    p2 = mgr.add("file.write", {"path": "/b"}, {})

    result = mgr.batch_approve([p1, p2, "nonexistent"])
    assert result["approved"] == 2
    assert result["total"] == 3
    assert executed == ["file.write", "file.write"]
    assert mgr.list_pending() == []


@pytest.mark.asyncio
async def test_batch_approve_handler_validation(monkeypatch):
    from backend.handlers._sandbox import _handle_sandbox_batch_approve

    empty = await _handle_sandbox_batch_approve(pending_ids=[])
    assert empty["success"] is False

    too_many = await _handle_sandbox_batch_approve(pending_ids=[str(i) for i in range(101)])
    assert too_many["success"] is False


def test_dispatcher_subscribes_sandbox_timeout():
    from services.notification_dispatcher import (
        DEFAULT_SUBSCRIBED_EVENTS,
        _EVENT_DEFAULT_PRIORITY,
        _EVENT_TEMPLATES,
    )

    assert "sandbox.timeout" in DEFAULT_SUBSCRIBED_EVENTS
    assert _EVENT_DEFAULT_PRIORITY["sandbox.timeout"] == "high"
    assert _EVENT_TEMPLATES["sandbox.timeout"]["title"]
