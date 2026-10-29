"""cascade_cleanup handlers 行为测试（T6.1，task 分支 + 兜底路径）。

覆盖 cascade_delete_task 的：cron 清理（remove_by_task_id / 兜底扫描）、
reminder 清理（remove_by_task_id / 兜底扫描）、KG、记忆索引（T5.1 双参）、
结构化记忆、单服务失败不阻塞其余。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.handlers._cascade_cleanup import cascade_delete_note, cascade_delete_task


# ── cascade_delete_task：cron 清理 ──


def test_task_cleans_cron_via_remove_by_task_id():
    class FakeCron:
        def __init__(self):
            self.removed = []

        def remove_by_task_id(self, tid):
            self.removed.append(tid)

    cron = FakeCron()
    ctx = SimpleNamespace(
        cron_service=cron, reminder_service=None, knowledge_graph_store=None, memory_index_service=None, structured_memory_store=None
    )
    result = asyncio.run(cascade_delete_task(ctx, "task_1"))
    assert result["cron"] == "deleted"
    assert cron.removed == ["task_1"]


def test_task_cleans_cron_via_fallback_scan():
    """无 remove_by_task_id 时兜底扫描 list_jobs 按 task_id 匹配删除。"""
    removed = []

    class FakeCron:
        def list_jobs(self):
            return [
                {"id": "c1", "params": {"task_id": "task_2"}},
                {"id": "c2", "params": {"other": 1}, "metadata": {"task_id": "task_2"}},
                {"id": "c3", "params": {"task_id": "other"}},
            ]

        def remove(self, cid):
            removed.append(cid)

    ctx = SimpleNamespace(
        cron_service=FakeCron(), reminder_service=None, knowledge_graph_store=None, memory_index_service=None, structured_memory_store=None
    )
    result = asyncio.run(cascade_delete_task(ctx, "task_2"))
    assert "deleted" in result["cron"]
    assert sorted(removed) == ["c1", "c2"]


def test_task_cleans_reminder_via_remove_by_task_id():
    class FakeReminder:
        def __init__(self):
            self.removed = []

        def remove_by_task_id(self, tid):
            self.removed.append(tid)

    rem = FakeReminder()
    ctx = SimpleNamespace(
        cron_service=None, reminder_service=rem, knowledge_graph_store=None, memory_index_service=None, structured_memory_store=None
    )
    result = asyncio.run(cascade_delete_task(ctx, "task_3"))
    assert result["reminder"] == "deleted"
    assert rem.removed == ["task_3"]


def test_task_cleans_reminder_via_fallback_scan():
    removed = []

    class FakeReminder:
        def list_alarms(self):
            return [
                {"id": "a1", "task_id": "task_4"},
                {"id": "a2", "task_id": "other"},
            ]

        def delete_alarm(self, aid):
            removed.append(aid)

    ctx = SimpleNamespace(
        cron_service=None, reminder_service=FakeReminder(), knowledge_graph_store=None, memory_index_service=None, structured_memory_store=None
    )
    result = asyncio.run(cascade_delete_task(ctx, "task_4"))
    assert "deleted" in result["reminder"]
    assert removed == ["a1"]


def test_task_cleans_memory_index_with_type():
    """T5.1：cascade_delete_task 应传 ("task", task_id) 给双参签名。"""
    deleted = []

    class FakeMemoryIndex:
        def delete_entries_by_source(self, stype, sid):
            deleted.append((stype, sid))
            return 2

    ctx = SimpleNamespace(
        cron_service=None, reminder_service=None, knowledge_graph_store=None, memory_index_service=FakeMemoryIndex(), structured_memory_store=None
    )
    result = asyncio.run(cascade_delete_task(ctx, "task_5"))
    assert result["memory_index"] == "deleted:2"
    assert deleted == [("task", "task_5")]


def test_task_cleans_structured_memory():
    cleaned = []

    class FakeStructured:
        def cleanup_relations_for_target(self, tid):
            cleaned.append(tid)

    ctx = SimpleNamespace(
        cron_service=None, reminder_service=None, knowledge_graph_store=None, memory_index_service=None, structured_memory_store=FakeStructured()
    )
    result = asyncio.run(cascade_delete_task(ctx, "task_6"))
    assert result["structured_memory"] == "deleted"
    assert cleaned == ["task_6"]


def test_task_single_failure_does_not_block_others():
    """KG 失败不应阻塞 cron/记忆索引清理（best-effort）。

    PD-2（2026-09-17）契约反转：FakeKG 改用真实 API 面（remove_by_source），
    旧版模拟的 delete_entity 在真实 store 上根本不存在。
    """

    class FailingKG:
        def remove_by_source(self, stype, sid):
            raise RuntimeError("kg down")

        def remove_entity(self, eid):
            raise RuntimeError("kg down")

    deleted = []

    class FakeMemoryIndex:
        def delete_entries_by_source(self, stype, sid):
            deleted.append(sid)
            return 1

    ctx = SimpleNamespace(
        cron_service=None,
        reminder_service=None,
        knowledge_graph_store=FailingKG(),
        memory_index_service=FakeMemoryIndex(),
        structured_memory_store=None,
    )
    result = asyncio.run(cascade_delete_task(ctx, "task_7"))
    assert "failed" in result["knowledge_graph"]
    assert result["memory_index"] == "deleted:1"
    assert deleted == ["task_7"]


# ── cascade_delete_note：KG 兜底路径（无 delete_entity 时直接操作 _entities）──


def test_note_kg_fallback_direct_entities():
    """无公共 API 的对象走兜底直删（PD-2 新契约：结果携带删除计数）。"""

    class FakeKG:
        def __init__(self):
            self._entities = {"note_8": {"id": "note_8"}}
            self._relations = [{"source": "note_8", "target": "x"}]

    kg = FakeKG()
    ctx = SimpleNamespace(knowledge_graph_store=kg, memory_index_service=None, structured_memory_store=None)
    result = asyncio.run(cascade_delete_note(ctx, "note_8"))
    assert result["knowledge_graph"] == "deleted:1"
    assert "note_8" not in kg._entities
    assert kg._relations == []


def test_note_no_kg_no_services_returns_empty():
    ctx = SimpleNamespace(knowledge_graph_store=None, memory_index_service=None, structured_memory_store=None)
    result = asyncio.run(cascade_delete_note(ctx, "note_9"))
    assert result == {}
