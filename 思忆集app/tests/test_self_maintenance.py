# -*- coding: utf-8 -*-
"""P2-7：监督式自优化测试 —— 巡检提案 + 审批执行 + 白名单硬边界。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.self_maintenance import SelfMaintenanceService


class FakeDataStore:
    def __init__(self):
        self.data: dict = {}

    def load_json(self, key, default=None):
        return self.data.get(key, default)

    def save_json(self, key, value):
        self.data[key] = value


class FakeTaskQueue:
    def __init__(self, completed=0):
        self._completed = completed
        self.purged_calls: list[float] = []

    def stats(self, queue_name=None):
        return {"completed": self._completed, "pending": 1}

    def purge_completed(self, *, older_than_seconds=86400.0):
        self.purged_calls.append(older_than_seconds)
        n = self._completed
        self._completed = 0
        return n


def _ctx_with_queue(completed=0):
    queue = FakeTaskQueue(completed=completed)
    ctx = SimpleNamespace(task_queue=queue, autonomous_assistant_manager=None,
                          local_data_store=None)
    return ctx, queue


def test_patrol_proposes_purge_when_queue_has_many_completed():
    store = FakeDataStore()
    svc = SelfMaintenanceService(store)
    ctx, _ = _ctx_with_queue(completed=50)

    proposals = svc.patrol(ctx)
    assert len(proposals) == 1
    assert proposals[0]["action"] == "purge_completed_queue_tasks"
    assert proposals[0]["status"] == "pending"


def test_patrol_no_proposal_below_threshold():
    store = FakeDataStore()
    svc = SelfMaintenanceService(store)
    ctx, _ = _ctx_with_queue(completed=5)

    assert svc.patrol(ctx) == []


def test_patrol_dedupes_open_proposals():
    store = FakeDataStore()
    svc = SelfMaintenanceService(store)
    ctx, _ = _ctx_with_queue(completed=50)

    first = svc.patrol(ctx)
    second = svc.patrol(ctx)  # 已有 pending 提案 → 不重复生成
    assert len(first) == 1
    assert second == []


def test_approve_executes_whitelisted_action_and_records():
    store = FakeDataStore()
    svc = SelfMaintenanceService(store)
    ctx, queue = _ctx_with_queue(completed=50)

    proposals = svc.patrol(ctx)
    pid = proposals[0]["proposal_id"]

    result = svc.approve(pid, ctx)
    assert result["success"] is True
    assert result["result"]["purged"] == 50
    assert result["result"]["completed_after"] == 0
    assert queue.purged_calls == [86400.0]

    stored = {p["proposal_id"]: p for p in svc.list_proposals()}
    assert stored[pid]["status"] == "executed"
    assert stored[pid]["result"]["purged"] == 50


def test_approve_rejects_non_whitelisted_action():
    store = FakeDataStore()
    svc = SelfMaintenanceService(store)
    # 手工伪造一个非白名单动作提案（模拟篡改/越权）
    store.data["self_maintenance_proposals"] = [{
        "proposal_id": "evil1",
        "action": "modify_source_code",
        "reason": "x",
        "detail": {},
        "status": "pending",
        "created_at": 0,
        "result": None,
    }]
    ctx, _ = _ctx_with_queue()
    result = svc.approve("evil1", ctx)
    assert result["success"] is False
    assert "not whitelisted" in result["error"]


def test_reject_marks_proposal_rejected():
    store = FakeDataStore()
    svc = SelfMaintenanceService(store)
    ctx, _ = _ctx_with_queue(completed=50)
    pid = svc.patrol(ctx)[0]["proposal_id"]

    result = svc.reject(pid)
    assert result["success"] is True
    stored = {p["proposal_id"]: p for p in svc.list_proposals()}
    assert stored[pid]["status"] == "rejected"


def test_approve_unknown_proposal_fails():
    store = FakeDataStore()
    svc = SelfMaintenanceService(store)
    ctx, _ = _ctx_with_queue()
    result = svc.approve("nonexistent", ctx)
    assert result["success"] is False


def test_archive_proposal_throttled_by_interval():
    store = FakeDataStore()
    svc = SelfMaintenanceService(store)
    cm = SimpleNamespace(archive_old_checkpoints=lambda: 3)
    tm = SimpleNamespace(_checkpoint_manager=cm)
    ctx = SimpleNamespace(task_queue=None,
                          autonomous_assistant_manager=SimpleNamespace(task_manager=tm),
                          local_data_store=None)

    first = svc.patrol(ctx)
    actions = [p["action"] for p in first]
    assert "archive_old_checkpoints" in actions

    # 未执行归档前，重复巡检不重复提案（pending 去重）
    second = svc.patrol(ctx)
    assert all(p["action"] != "archive_old_checkpoints" for p in second)
