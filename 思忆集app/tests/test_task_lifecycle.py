"""AgentTaskLifecycle 单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.models import PlannedStep
from assistant.task_lifecycle import AgentTaskLifecycle
from data.local_storage import LocalDataStore


def _make_lifecycle(tmp_path, *, user_profile_service=None):
    store = LocalDataStore(base_dir=str(tmp_path))
    memory = SimpleNamespace(
        append_decision=lambda *a, **kw: None,
        get_user_profile_snapshot=lambda: {"preferred_delivery": "desktop"},
    )
    return AgentTaskLifecycle(
        data_store=store,
        memory=memory,
        user_profile_service=user_profile_service,
    )


def test_resolve_delivery_channel_explicit():
    assert AgentTaskLifecycle.resolve_delivery_channel("inbox", {}) == "inbox"


def test_resolve_delivery_channel_from_profile():
    assert AgentTaskLifecycle.resolve_delivery_channel(None, {"preferred_delivery": "desktop"}) == "desktop"


def test_resolve_delivery_channel_default():
    assert AgentTaskLifecycle.resolve_delivery_channel(None, {}) == "chat"


def test_resolve_task_status_variants():
    assert AgentTaskLifecycle.resolve_task_status({"ok": 3, "failed": 0}) == "completed"
    assert AgentTaskLifecycle.resolve_task_status({"ok": 0, "failed": 1}) == "failed"
    assert AgentTaskLifecycle.resolve_task_status({"ok": 0, "blocked": 1, "failed": 0}) == "blocked"
    assert AgentTaskLifecycle.resolve_task_status({"ok": 0, "degraded": 1, "failed": 0}) == "degraded"


def test_build_and_persist(tmp_path):
    lc = _make_lifecycle(tmp_path)
    task = lc.build_agent_task("test goal", source="test")
    assert task.goal == "test goal"
    assert task.delivery_channel == "chat"
    lc.persist_agent_task(task)
    # no exception


def test_get_user_profile_snapshot_from_service(tmp_path):
    svc = SimpleNamespace(build_routing_context=lambda: {"lang": "zh"})
    lc = _make_lifecycle(tmp_path, user_profile_service=svc)
    assert lc.get_user_profile_snapshot() == {"lang": "zh"}


def test_get_user_profile_snapshot_fallback_memory(tmp_path):
    lc = _make_lifecycle(tmp_path)
    assert lc.get_user_profile_snapshot() == {"preferred_delivery": "desktop"}


def test_execute_and_record(tmp_path):
    lc = _make_lifecycle(tmp_path)
    steps = [PlannedStep(step_id="s1", action_id="noop", payload={})]
    executor = SimpleNamespace(
        execute_plan=lambda steps, authorize_callback, task, delivery_channel=None, user_profile=None, cancellation_event=None: {
            "ok": 1, "failed": 0, "delivery": {},
        }
    )
    summary = lc.execute_and_record(
        goal="test",
        steps=steps,
        source="test",
        executor=executor,
        authorize_callback=lambda *a, **kw: {"allowed": True},
    )
    assert summary["task"]["status"] == "completed"
    assert summary["ok"] == 1
