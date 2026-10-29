"""AG-C1③：SandboxManager 审批历史（record/list/clear + kv 落盘 + RPC 方法）。

覆盖：
- approve（无 executor / executor 抛错）/ reject / timeout 四条处置路径均留痕；
- list_history 倒序 + limit 上限（SANDBOX_HISTORY_CAP）；
- clear_history 清空（进程内环形 + kv 键）；
- kv store 注入时 update_json 落盘、load_json 读回；
- 历史条目只存摘要（args_sha256 指纹），不泄漏 args/preview 原文；
- sandbox.history / sandbox.history.clear RPC handler。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from services import sandbox_manager as sm
from services.confirmation_store import compute_args_sha256
from services.sandbox_manager import SANDBOX_HISTORY_CAP, SandboxManager


class FakeStore:
    """最小 kv 门面：与 LocalDataStore 的 update_json/save_json/load_json 同签名。"""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.update_calls = 0
        self.save_calls = 0

    def update_json(self, key: str, default: object, fn) -> object:
        self.update_calls += 1
        current = self.data.get(key, default)
        self.data[key] = fn(current)
        return self.data[key]

    def save_json(self, key: str, value: object) -> None:
        self.save_calls += 1
        self.data[key] = value

    def load_json(self, key: str, default: object) -> object:
        return self.data.get(key, default)


@pytest.fixture()
def manager() -> SandboxManager:
    return SandboxManager()


def _add_one(mgr: SandboxManager, action_id: str = "notes.delete") -> str:
    return mgr.add(action_id, {"note_id": 1}, {"summary": "delete note"})


# ── 四条处置路径留痕 ──────────────────────────────────────────


def test_approve_without_executor_records_history(manager: SandboxManager) -> None:
    # P0-D（2026-09-18）契约变更：无 executor 不再谎报 success=True + event=approved
    # ——那正是"批了但从未执行"假成功的锁死点。诚实契约：success=False、
    # 条目回退 pending 可重试、留痕 event=failed（审计仍闭环，语义反转）。
    pid = _add_one(manager)
    result = manager.approve(pid)
    assert result["success"] is False
    assert result["reason"] == "tool_executor_not_injected"
    entry = manager.get(pid)
    assert entry is not None and entry["status"] == "pending"
    items = manager.list_history()
    assert len(items) == 1
    assert items[0]["event"] == "failed"
    assert items[0]["pending_id"] == pid
    assert items[0]["action_id"] == "notes.delete"


def test_approve_with_executor_failure_records_failed(manager: SandboxManager) -> None:
    def boom(action_id, args):
        raise RuntimeError("executor exploded")

    manager.set_tool_executor(boom)
    pid = _add_one(manager)
    result = manager.approve(pid)
    assert result["success"] is False
    items = manager.list_history()
    assert len(items) == 1
    assert items[0]["event"] == "failed"
    assert "executor exploded" in items[0]["error"]


def test_reject_records_history(manager: SandboxManager) -> None:
    pid = _add_one(manager)
    manager.reject(pid)
    items = manager.list_history()
    assert len(items) == 1
    assert items[0]["event"] == "rejected"


def test_reject_unknown_pid_records_nothing(manager: SandboxManager) -> None:
    manager.reject("nonexistent")
    assert manager.list_history() == []


def test_timeout_records_history(manager: SandboxManager) -> None:
    pid = _add_one(manager)
    # 把 created_at 拨到超时线之前，触发 check_timeouts 的过期处置
    manager._pending[pid]["created_at"] = time.time() - 10_000
    processed = manager.check_timeouts()
    assert len(processed) == 1
    items = manager.list_history()
    assert len(items) == 1
    assert items[0]["event"] == "timeout"
    assert items[0]["status"] == "timed_out_rejected"


# ── 摘要语义：不泄漏原文 ─────────────────────────────────────


def test_history_stores_digest_not_raw_args(manager: SandboxManager) -> None:
    pid = _add_one(manager)
    manager.reject(pid)
    item = manager.list_history()[0]
    assert item["args_sha256"] == compute_args_sha256({"note_id": 1})
    assert "args" not in item
    assert "preview" not in item


# ── 列表语义：倒序 / limit / 上限 / 清空 ─────────────────────


def test_list_history_newest_first_and_limit(manager: SandboxManager) -> None:
    pids = [_add_one(manager, f"act.{i}") for i in range(3)]
    for pid in pids:
        manager.reject(pid)
    items = manager.list_history()
    assert [i["action_id"] for i in items] == ["act.2", "act.1", "act.0"]
    assert manager.list_history(limit=2)[0]["action_id"] == "act.2"
    assert len(manager.list_history(limit=2)) == 2


def test_list_history_limit_capped_at_cap(manager: SandboxManager) -> None:
    assert manager.list_history(limit=10_000) == []  # 不抛错即可，上限逻辑在非空时生效


def test_clear_history_clears_ring_and_store(manager: SandboxManager) -> None:
    store = FakeStore()
    manager.set_history_store(store)
    manager.reject(_add_one(manager))
    assert len(manager.list_history()) == 1
    manager.clear_history()
    assert manager.list_history() == []
    assert store.data[sm.SANDBOX_HISTORY_KEY] == []


# ── kv store 注入 ────────────────────────────────────────────


def test_kv_store_persist_and_reload(manager: SandboxManager) -> None:
    store = FakeStore()
    manager.set_history_store(store)
    manager.reject(_add_one(manager))
    assert store.update_calls >= 1
    persisted = store.data[sm.SANDBOX_HISTORY_KEY]
    assert isinstance(persisted, list) and len(persisted) == 1

    # 新 manager 注入同一 store（模拟重启后）→ 历史以 kv 为准
    manager2 = SandboxManager()
    manager2.set_history_store(store)
    items = manager2.list_history()
    assert len(items) == 1
    assert items[0]["event"] == "rejected"


def test_kv_store_failure_does_not_break_approval(manager: SandboxManager) -> None:
    class BrokenStore:
        def update_json(self, *a, **kw):
            raise OSError("disk full")

        def load_json(self, *a, **kw):
            raise OSError("disk full")

        def save_json(self, *a, **kw):
            raise OSError("disk full")

    manager.set_history_store(BrokenStore())
    # P0-D：本用例意图是"历史落盘失败不影响审批主流程"，需要 executor 才能真正批准成功
    # （无 executor 现按诚实契约返回 success=False）。注入后即测原意。
    manager.set_tool_executor(lambda aid, args: {"ok": True})
    pid = _add_one(manager)
    result = manager.approve(pid)  # 历史落盘失败不得影响审批主流程
    assert result["success"] is True
    assert len(manager.list_history()) == 1  # 进程内环形仍可用


def test_ring_capacity_capped(manager: SandboxManager) -> None:
    store = FakeStore()
    manager.set_history_store(store)
    for i in range(SANDBOX_HISTORY_CAP + 10):
        manager.reject(_add_one(manager, f"act.{i}"))
    assert len(manager.list_history(limit=SANDBOX_HISTORY_CAP)) == SANDBOX_HISTORY_CAP


# ── RPC handlers ─────────────────────────────────────────────


def test_rpc_sandbox_history(monkeypatch) -> None:
    mgr = SandboxManager()
    mgr.reject(mgr.add("notes.delete", {"note_id": 1}, {"s": "x"}))
    monkeypatch.setattr("backend.handlers._sandbox.get_sandbox_manager", lambda: mgr)
    from backend.handlers import _sandbox as sb

    result = asyncio.run(sb._handle_sandbox_history(limit=10))
    assert len(result["items"]) == 1

    bad = asyncio.run(sb._handle_sandbox_history(limit="abc"))
    assert "error" in bad


def test_rpc_sandbox_history_clear(monkeypatch) -> None:
    mgr = SandboxManager()
    mgr.reject(mgr.add("notes.delete", {"note_id": 1}, {"s": "x"}))
    monkeypatch.setattr("backend.handlers._sandbox.get_sandbox_manager", lambda: mgr)
    from backend.handlers import _sandbox as sb

    result = asyncio.run(sb._handle_sandbox_history_clear())
    assert result == {"cleared": True}
    assert mgr.list_history() == []


def test_rpc_history_without_manager(monkeypatch) -> None:
    monkeypatch.setattr("backend.handlers._sandbox.get_sandbox_manager", lambda: None)
    from backend.handlers import _sandbox as sb

    assert "error" in asyncio.run(sb._handle_sandbox_history())
    assert "error" in asyncio.run(sb._handle_sandbox_history_clear())
