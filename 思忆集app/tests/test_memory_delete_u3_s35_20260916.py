# -*- coding: utf-8 -*-
"""U3（S3-5，2026-09-16）：记忆可删可溯源 —— memory.delete RPC + 软删全链路。

后端此前**根本没有 memory.delete RPC**（_chat_memory.py 只有
search/recent/related/save），且存在三个软删半截缺陷：
1. revoke_memory 只挡 legacy FTS 路径 —— hybrid recall 走引擎照常召回；
2. _sync_memory_search 把 revoked 条目原样同步进引擎；
3. rebuild_index 全量覆盖写会冲掉 revoked 标记 → 被撤销的记忆"复活"。
另 _format_entry 对 hybrid 引擎返回的条目（只有 id 没有 entry_id）不补写
→ 前端删除按钮拿不到 id（方案 §6"依赖 entry_id 补写小项"的真相）。

memory.delete 采用软删（revoke）语义：条目保留在索引 JSON、审计留痕、可恢复；
物理硬删留待回收站专门批次。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from memory.index import AssistantMemoryIndexService


def _make_index(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    return AssistantMemoryIndexService(data_store=store), store


def _entry(eid: str, content: str) -> dict:
    return {
        "entry_id": eid,
        "source_type": "memory",
        "source_id": eid.split(":", 1)[1],
        "title": content[:20],
        "content": content,
        "summary": "",
        "tags_text": "",
        "updated_at": "2026-09-16T00:00:00+00:00",
        "metadata": {},
    }


def _seed(service, entries):
    service.data_store.save_json(
        "assistant_memory_index",
        {"entries": entries, "built_at": "2026-09-16T00:00:00+00:00", "source_counts": {"memory": len(entries)}},
    )


# ── 1. 软删对召回路径不可见 ──


def test_revoked_entry_invisible_in_legacy_recall(tmp_path):
    svc, _store = _make_index(tmp_path)
    _seed(svc, [_entry("memory:a", "保留的记忆"), _entry("memory:b", "要删的记忆")])
    assert svc.revoke_memory("memory:b", reason="测试删除")
    result = svc.recall("记忆", top_k=10, rebuild_if_missing=False)
    ids = [it["entry_id"] for it in result["items"]]
    assert "memory:a" in ids
    assert "memory:b" not in ids, "软删条目必须从召回结果消失"


# ── 2. revoke 触发引擎重同步（剔除 revoked） ──


class _RecordingEngine:
    def __init__(self):
        self.synced: list[list] = []

    def sync_entries(self, entries):
        self.synced.append(list(entries))
        return len(entries)

    def get_entry_count(self):
        return 0

    def search(self, **kwargs):
        return {"items": [], "mode": "test"}


def test_revoke_sets_pending_sync_and_sync_filters_revoked(tmp_path):
    engine = _RecordingEngine()
    store = LocalDataStore(base_dir=str(tmp_path))
    svc = AssistantMemoryIndexService(data_store=store, memory_search=engine)
    _seed(svc, [_entry("memory:a", "保留"), _entry("memory:b", "要删")])
    assert svc.revoke_memory("memory:b")
    assert svc._pending_sync is True, "revoke 后必须置 pending_sync 让引擎剔除 revoked"
    svc._sync_memory_search()
    assert engine.synced, "sync 必须被调用（含全撤销清空场景）"
    synced_ids = [e.get("entry_id") for e in engine.synced[-1]]
    assert "memory:a" in synced_ids
    assert "memory:b" not in synced_ids, "revoked 条目不得进入检索引擎"


def test_all_revoked_still_syncs_to_clear_engine(tmp_path):
    """全部撤销时也必须调 sync_entries([]) 清空引擎残留。"""
    engine = _RecordingEngine()
    store = LocalDataStore(base_dir=str(tmp_path))
    svc = AssistantMemoryIndexService(data_store=store, memory_search=engine)
    _seed(svc, [_entry("memory:b", "唯一一条")])
    svc.revoke_memory("memory:b")
    svc._sync_memory_search()
    assert engine.synced and engine.synced[-1] == [], "全撤销必须以空列表清引擎"


# ── 3. rebuild_index 不再冲掉 revoked 标记（复活 bug） ──


class _FakeNoteRepo:
    def __init__(self, notes):
        self._notes = notes

    def list_notes(self, include_deleted=False):
        return list(self._notes)


def test_rebuild_preserves_revoked_marks(tmp_path):
    """软删一条笔记派生记忆后 rebuild（会重新 _collect_entries），标记必须保留，
    否则被撤销的记忆在下一次索引重建后"复活"重新可召回。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    notes = [
        {"id": "1", "title": "保留笔记", "content": "甲内容", "updated_at": "2026-09-16T00:00:00"},
        {"id": "2", "title": "要删笔记", "content": "乙内容", "updated_at": "2026-09-16T00:00:00"},
    ]
    svc = AssistantMemoryIndexService(data_store=store, note_repository=_FakeNoteRepo(notes))
    svc.rebuild_index()  # 首次建索引：note:1 / note:2
    assert svc.revoke_memory("note:2")
    svc.rebuild_index()  # 重新收集会再次产出 note:2 —— 旧实现会丢 revoked 标记

    state = svc.data_store.load_json("assistant_memory_index", {"entries": []})
    by_id = {e["entry_id"]: e for e in state.get("entries") or []}
    assert "note:2" in by_id, "note:2 应仍被重新收集进来"
    assert (by_id["note:2"].get("metadata") or {}).get("revoked") is True, "rebuild 必须继承 revoked 标记"
    assert not (by_id["note:1"].get("metadata") or {}).get("revoked"), "未撤销条目不受影响"
    ids = [it["entry_id"] for it in svc.recall("笔记", top_k=10, rebuild_if_missing=False)["items"]]
    assert "note:2" not in ids
    assert "note:1" in ids


# ── 4. _format_entry 归一补写 entry_id（hybrid 引擎只有 id） ──


def test_format_entry_normalizes_id_to_entry_id():
    # hybrid 引擎返回形状：只有 id
    item = AssistantMemoryIndexService._format_entry({"id": "memory:x", "content": "内容"}, "")
    assert item["entry_id"] == "memory:x", "引擎条目的 id 必须归一补写为 entry_id"
    # legacy JSON 条目：本来就有 entry_id
    item2 = AssistantMemoryIndexService._format_entry({"entry_id": "memory:y", "id": "ignored", "content": ""}, "")
    assert item2["entry_id"] == "memory:y", "entry_id 优先于 id"


# ── 5. memory.delete RPC 端到端 ──


@pytest.mark.asyncio
async def test_rpc_memory_delete_revokes_and_excludes_from_recall(tmp_path):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    svc, _store = _make_index(tmp_path)
    _seed(svc, [_entry("memory:a", "保留"), _entry("memory:b", "要删")])
    ctx = SimpleNamespace(memory_index_service=svc)
    reg = MethodRegistry()
    register_chat_memory(reg, ctx)
    handler = reg._handlers["memory.delete"]

    r = await handler(entry_id="memory:b", reason="测试")
    assert r["success"] is True
    assert r["mode"] == "revoked"
    recall = svc.recall("要删", top_k=5, rebuild_if_missing=False)
    assert "memory:b" not in [it["entry_id"] for it in recall["items"]]


@pytest.mark.asyncio
async def test_rpc_memory_delete_missing_entry_id(tmp_path):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    svc, _store = _make_index(tmp_path)
    ctx = SimpleNamespace(memory_index_service=svc)
    reg = MethodRegistry()
    register_chat_memory(reg, ctx)
    handler = reg._handlers["memory.delete"]

    r = await handler(entry_id="")
    assert r["success"] is False
    assert "entry_id" in r["error"]


@pytest.mark.asyncio
async def test_rpc_memory_delete_unknown_entry(tmp_path):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    svc, _store = _make_index(tmp_path)
    _seed(svc, [_entry("memory:a", "保留")])
    ctx = SimpleNamespace(memory_index_service=svc)
    reg = MethodRegistry()
    register_chat_memory(reg, ctx)
    handler = reg._handlers["memory.delete"]

    r = await handler(entry_id="memory:ghost")
    assert r["success"] is False
    assert "ghost" in r["error"]


@pytest.mark.asyncio
async def test_rpc_memory_delete_service_unavailable():
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    ctx = SimpleNamespace(memory_index_service=None)
    reg = MethodRegistry()
    register_chat_memory(reg, ctx)
    handler = reg._handlers["memory.delete"]

    r = await handler(entry_id="memory:a")
    assert r["success"] is False
    assert "记忆服务" in r["error"] or "service" in r["error"].lower()
