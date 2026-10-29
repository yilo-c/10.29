"""AuditRollback 单元测试：AuditTrail / RollbackManager / WriteGuard。"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from core.audit_rollback import AuditTrail, RollbackManager, WriteGuard


class _MockDataStore:
    """内存 data_store，模拟 load_json / save_json。"""

    def __init__(self):
        self._store = {}

    def load_json(self, key, default=None):
        return self._store.get(key, default)

    def save_json(self, key, data):
        self._store[key] = data


class _MockNoteRepo:
    def __init__(self):
        self.notes = {}

    def get_note(self, nid):
        return self.notes.get(nid)

    def update_note(self, nid, data):
        self.notes[nid] = {**self.notes.get(nid, {}), **data}
        return True

    def add_note(self, data):
        nid = data.get("id", str(len(self.notes) + 1))
        self.notes[nid] = dict(data)
        return nid

    def delete_note(self, nid):
        self.notes.pop(nid, None)


class _MockTaskRepo:
    def __init__(self):
        self.tasks = {}

    def get_task(self, tid):
        return self.tasks.get(tid)

    def update_task(self, tid, data):
        self.tasks[tid] = {**self.tasks.get(tid, {}), **data}
        return True

    def add_task(self, data):
        tid = data.get("id", str(len(self.tasks) + 1))
        self.tasks[tid] = dict(data)
        return tid

    def delete_task(self, tid):
        self.tasks.pop(tid, None)


# ── Fixtures ──


@pytest.fixture
def mock_store():
    return _MockDataStore()


@pytest.fixture
def audit_trail(mock_store):
    return AuditTrail(data_store=mock_store)


@pytest.fixture
def rollback_manager(mock_store, audit_trail):
    return RollbackManager(
        data_store=mock_store,
        audit_trail=audit_trail,
        note_repository=_MockNoteRepo(),
        task_repository=_MockTaskRepo(),
    )


# ═══════════════════════════════════════════════════════════
# AuditTrail
# ═══════════════════════════════════════════════════════════


class TestAuditTrail:
    def test_record_intent_and_complete(self, audit_trail):
        """记录写入意图 → 标记完成 → 可查询到完整审计条目。"""
        entry_id = audit_trail.record_write_intent(
            actor="test_user",
            action_id="test.action",
            target_type="note",
            target_id="n001",
            operation="write",
            before_snapshot={"title": "旧标题", "content": "旧内容"},
        )
        assert entry_id, "应返回非空的 entry_id"

        ok = audit_trail.record_write_complete(entry_id, after_snapshot={"title": "新标题", "content": "新内容"})
        assert ok is True

        entry = audit_trail.get_entry(entry_id)
        assert entry is not None
        assert entry["status"] == "completed"
        assert entry["after_snapshot"]["title"] == "新标题"
        assert entry["target_type"] == "note"

    def test_record_intent_and_failed(self, audit_trail):
        """记录写入意图 → 标记失败 → status 为 failed。"""
        entry_id = audit_trail.record_write_intent(
            actor="test_user",
            action_id="test.action",
            target_type="task",
            before_snapshot={"name": "旧任务"},
        )
        ok = audit_trail.record_write_failed(entry_id, error="写入超时")
        assert ok is True

        entry = audit_trail.get_entry(entry_id)
        assert entry["status"] == "failed"
        assert "写入超时" in entry.get("error", "")

    def test_record_write_failed_nonexistent_entry(self, audit_trail):
        """更新不存在的条目应返回 False。"""
        ok = audit_trail.record_write_failed("nonexistent_id")
        assert ok is False

    def test_list_entries_with_filters(self, audit_trail):
        """list_entries 支持按 action_id 和 status 过滤。"""
        e1 = audit_trail.record_write_intent(actor="a", action_id="act.1", target_type="note")
        e2 = audit_trail.record_write_intent(actor="a", action_id="act.2", target_type="note")
        audit_trail.record_write_complete(e1)
        audit_trail.record_write_failed(e2)

        all_entries = audit_trail.list_entries(limit=10)
        assert len(all_entries) >= 2

        act1_entries = audit_trail.list_entries(action_id="act.1", limit=10)
        assert all(e["action_id"] == "act.1" for e in act1_entries)

        failed_entries = audit_trail.list_entries(status="failed", limit=10)
        assert all(e["status"] == "failed" for e in failed_entries)

    def test_list_rollbackable(self, audit_trail):
        """list_rollbackable 只返回 completed 且有 before_snapshot 的条目。"""
        audit_trail.record_write_intent(actor="a", action_id="act.1", target_type="note", before_snapshot=None)
        e2 = audit_trail.record_write_intent(actor="a", action_id="act.2", target_type="note", before_snapshot={"x": 1})
        audit_trail.record_write_complete(e2)

        rollbackable = audit_trail.list_rollbackable(limit=10)
        assert len(rollbackable) == 1
        assert rollbackable[0]["entry_id"] == e2

    def test_list_rollbackable_excludes_failed(self, audit_trail):
        """失败的条目不应出现在可回滚列表中。"""
        eid = audit_trail.record_write_intent(actor="a", action_id="act.1", target_type="note", before_snapshot={"x": 1})
        audit_trail.record_write_failed(eid)
        assert len(audit_trail.list_rollbackable(limit=10)) == 0

    def test_max_trail_size(self, mock_store):
        """超过 MAX_TRAIL_SIZE 的条目应被截断。"""
        trail = AuditTrail(data_store=mock_store)
        small_max = 10
        trail.MAX_TRAIL_SIZE = small_max

        for i in range(small_max + 5):
            eid = trail.record_write_intent(actor="u", action_id=f"a.{i}", target_type="note")
            trail.record_write_complete(eid)

        entries = trail.list_entries(limit=100)
        assert len(entries) <= small_max

    def test_safe_copy_preserves_data(self):
        """_safe_copy 应返回独立深拷贝。"""
        original = {"a": [1, 2, {"b": 3}]}
        copied = AuditTrail._safe_copy(original)
        assert copied == original
        copied["a"][2]["b"] = 99
        assert original["a"][2]["b"] == 3  # 原数据不受影响

    def test_safe_copy_none(self):
        assert AuditTrail._safe_copy(None) is None


# ═══════════════════════════════════════════════════════════
# RollbackManager
# ═══════════════════════════════════════════════════════════


class TestRollbackManager:
    def test_rollback_note_restores_snapshot(self, rollback_manager, audit_trail):
        """创建笔记快照 → 修改 → 回滚 → 验证恢复。"""
        note_repo = rollback_manager.note_repository
        before = {"title": "原始标题", "content": "原始内容"}
        after = {"title": "修改后", "content": "新内容"}

        entry_id = audit_trail.record_write_intent(
            actor="user", action_id="note.edit",
            target_type="note", target_id="n001",
            before_snapshot=before,
        )
        note_repo.add_note({"id": "n001", **after})
        audit_trail.record_write_complete(entry_id, after_snapshot=after)

        result = rollback_manager.rollback(entry_id)
        assert result["rolled_back"] is True

        restored = note_repo.get_note("n001")
        assert restored["title"] == "原始标题"
        assert restored["content"] == "原始内容"

    def test_can_rollback_checks_return_correct_info(self, rollback_manager, audit_trail):
        """can_rollback 对各种无效状态返回正确原因。"""
        # 不存在的条目
        info = rollback_manager.can_rollback("nonexistent")
        assert info["can_rollback"] is False
        assert info["reason"] == "entry_not_found"

        # 状态不是 completed
        eid = audit_trail.record_write_intent(actor="u", action_id="test", target_type="note", before_snapshot={"a": 1})
        info = rollback_manager.can_rollback(eid)
        assert info["can_rollback"] is False

        # before_snapshot 为 None
        eid2 = audit_trail.record_write_intent(actor="u", action_id="test", target_type="note")
        audit_trail.record_write_complete(eid2)
        info = rollback_manager.can_rollback(eid2)
        assert info["can_rollback"] is False

    def test_rollback_nonexistent_entry(self, rollback_manager):
        """回滚不存在的条目应返回错误。"""
        result = rollback_manager.rollback("ghost_entry")
        assert result["rolled_back"] is False
        assert "entry_not_found" in result.get("reason", "")

    def test_double_rollback_is_idempotent(self, rollback_manager, audit_trail):
        """第二次回滚应再次应用同一快照（idempotent）。"""
        entry_id = audit_trail.record_write_intent(
            actor="u", action_id="test",
            target_type="note", target_id="n002",
            before_snapshot={"title": "原始"},
        )
        note_repo = rollback_manager.note_repository
        note_repo.add_note({"id": "n002", "title": "原始"})
        audit_trail.record_write_complete(entry_id, after_snapshot={"title": "修改后"})

        first = rollback_manager.rollback(entry_id)
        assert first["rolled_back"] is True
        assert note_repo.get_note("n002")["title"] == "原始"

        # 第二次回滚：条目仍然是 completed + 有 before_snapshot，所以可再次回滚
        second = rollback_manager.rollback(entry_id)
        assert second["rolled_back"] is True
        assert note_repo.get_note("n002")["title"] == "原始"

    def test_batch_rollback(self, rollback_manager, audit_trail):
        """batch_rollback 应逐个执行并返回统计。"""
        ids = []
        for i in range(3):
            eid = audit_trail.record_write_intent(
                actor="u", action_id="batch",
                target_type="json_store", target_id=f"key_{i}",
                before_snapshot={"idx": i},
            )
            audit_trail.record_write_complete(eid)
            ids.append(eid)

        result = rollback_manager.batch_rollback(ids)
        assert result["total"] == 3
        assert result["rolled_back"] == 3

    def test_can_rollback_rejects_null_snapshot(self, rollback_manager, audit_trail):
        """before_snapshot 为 None 的条目不可回滚。"""
        task_repo = rollback_manager.task_repository
        task_repo.add_task({"id": "t001", "name": "待删除任务"})

        entry_id = audit_trail.record_write_intent(
            actor="u", action_id="task.delete",
            target_type="task", target_id="t001",
            before_snapshot=None,
        )
        audit_trail.record_write_complete(entry_id)

        info = rollback_manager.can_rollback(entry_id)
        assert info["can_rollback"] is False
        assert info["reason"] == "no_before_snapshot"

        result = rollback_manager.rollback(entry_id)
        assert result["rolled_back"] is False


# ═══════════════════════════════════════════════════════════
# WriteGuard
# ═══════════════════════════════════════════════════════════


class TestWriteGuard:
    def test_write_guard_success_path(self, audit_trail):
        """WriteGuard 上下文管理器 — 正常路径自动记录 complete。"""
        guard = WriteGuard(audit_trail)
        before = {"title": "原始"}
        after = {"title": "新标题"}

        with guard.protect("note", "n001", actor="user", action_id="edit", before_snapshot=before) as ctx:
            ctx.set_after(after)

        assert ctx.entry_id is not None
        entry = audit_trail.get_entry(ctx.entry_id)
        assert entry["status"] == "completed"
        assert entry["after_snapshot"]["title"] == "新标题"

    def test_write_guard_failure_path(self, audit_trail):
        """WriteGuard 上下文管理器 — 异常路径自动记录 failed。"""
        guard = WriteGuard(audit_trail)

        class _TestError(Exception):
            pass

        with pytest.raises(_TestError):
            with guard.protect("note", "n001", actor="user", action_id="edit", before_snapshot={"title": "x"}) as ctx:
                ctx.set_after({"title": "y"})
                raise _TestError("模拟失败")

        assert ctx.entry_id is not None
        entry = audit_trail.get_entry(ctx.entry_id)
        assert entry["status"] == "failed"
