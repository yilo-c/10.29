# -*- coding: utf-8 -*-
"""U3 批1（S3-5 记忆管理面板案，2026-09-16）：面板后端原语。

架构复审（deliverables/architecture-conformance-audit-2026-09-16.md §10）判定：
面板**不能**用 memory.recent 当数据源——它是 recall("") 的空查询兜底，即
「按 focus topics 相关性召回」（memory/index.py 的 effective_query 逻辑），
既非"最近"语义、又被 min(top_k,20) 卡死无翻页；且全仓没有任何"恢复"能力。
本批补齐三件：

1. ``AssistantMemoryIndexService.list_entries``：直读索引 JSON、updated_at 倒序、
   limit/offset 真分页、include_revoked 显隐 —— 真「最近记忆」列表。
2. ``AssistantMemoryIndexService.restore_memory``：与 revoke_memory 对称，
   清 revoked 标记族 + 审计 + _pending_sync（恢复后经同步重新进引擎）。
3. RPC ``memory.list`` / ``memory.restore``（register_with_schema）。
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


def _make_index(tmp_path, memory_search=None):
    store = LocalDataStore(base_dir=str(tmp_path))
    svc = AssistantMemoryIndexService(data_store=store, memory_search=memory_search)
    return svc, store


def _entry(eid: str, content: str, updated_at: str = "2026-09-16T00:00:00+00:00") -> dict:
    return {
        "entry_id": eid,
        "source_type": "memory",
        "source_id": eid.split(":", 1)[1],
        "title": content[:20],
        "content": content,
        "summary": "",
        "tags_text": "",
        "updated_at": updated_at,
        "metadata": {},
    }


def _seed(service, entries):
    service.data_store.save_json(
        "assistant_memory_index",
        {
            "entries": entries,
            "built_at": "2026-09-16T00:00:00+00:00",
            "source_counts": {"memory": len(entries)},
        },
    )


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


# ══════════════════════════════════════
# 1. list_entries：分页 / 排序 / revoked 显隐
# ══════════════════════════════════════


def test_list_entries_sorts_by_updated_at_desc(tmp_path):
    """真「最近」= updated_at 倒序，与 focus topics 无关（这是弃用 recent 的核心）。"""
    svc, _ = _make_index(tmp_path)
    _seed(
        svc,
        [
            _entry("memory:old", "最早", "2026-01-01T00:00:00+00:00"),
            _entry("memory:new", "最新", "2026-09-16T12:00:00+00:00"),
            _entry("memory:mid", "中间", "2026-06-01T00:00:00+00:00"),
        ],
    )
    r = svc.list_entries(limit=10, offset=0)
    assert [it["entry_id"] for it in r["items"]] == ["memory:new", "memory:mid", "memory:old"]
    assert r["total"] == 3 and r["total_revoked"] == 0
    # entry_id 归一透出（面板删除按钮要拿它）
    assert all(it.get("entry_id") for it in r["items"])


def test_list_entries_paginates_without_top20_cap(tmp_path):
    """审计 §10.2：必须能翻过 recall 面的 min(top_k,20) 硬上限。"""
    svc, _ = _make_index(tmp_path)
    _seed(
        svc,
        [_entry(f"memory:{i:03d}", f"内容{i}", f"2026-09-{(i % 28) + 1:02d}T00:00:00+00:00") for i in range(45)],
    )
    page1 = svc.list_entries(limit=25, offset=0)
    page2 = svc.list_entries(limit=25, offset=25)
    assert len(page1["items"]) == 25, "单页 25 条不得被截成 20"
    assert len(page2["items"]) == 20
    assert page1["total"] == 45
    ids = {it["entry_id"] for it in page1["items"]} | {it["entry_id"] for it in page2["items"]}
    assert len(ids) == 45, "分页不得重复或漏条目"


def test_list_entries_hides_revoked_by_default_and_exposes_on_demand(tmp_path):
    svc, _ = _make_index(tmp_path)
    _seed(svc, [_entry("memory:a", "保留"), _entry("memory:b", "要删"), _entry("memory:c", "也要删")])
    assert svc.revoke_memory("memory:b")
    assert svc.revoke_memory("memory:c")

    visible = svc.list_entries(limit=50, offset=0)
    assert [it["entry_id"] for it in visible["items"]] == ["memory:a"]
    assert visible["total"] == 1, "默认池不含 revoked（面板「生效中」tab）"
    assert visible["total_revoked"] == 2, "revoked 计数透出，供前端显示已撤销条数"

    including = svc.list_entries(limit=50, offset=0, include_revoked=True)
    assert including["total"] == 3
    by_id = {it["entry_id"]: it for it in including["items"]}
    assert by_id["memory:b"]["metadata"].get("revoked") is True, "已撤销 tab 要拿到 revoked 标记"
    assert by_id["memory:b"]["metadata"].get("revoked_at")
    assert not by_id["memory:a"]["metadata"].get("revoked")


def test_list_entries_empty_and_bad_params(tmp_path):
    svc, _ = _make_index(tmp_path)
    r = svc.list_entries()
    assert r == {"items": [], "total": 0, "total_revoked": 0}
    _seed(svc, [_entry("memory:a", "x")])
    # 非法 limit/offset 走安全默认，不抛
    assert svc.list_entries(limit="abc", offset=None)["total"] == 1
    assert svc.list_entries(limit=99999)["items"], "limit 应被夹到上限而非返回空"


# ══════════════════════════════════════
# 2. restore_memory：与 revoke 对称
# ══════════════════════════════════════


def test_restore_makes_entry_recallable_again(tmp_path):
    svc, _ = _make_index(tmp_path)
    _seed(svc, [_entry("memory:a", "保留"), _entry("memory:b", "删了又恢复")])
    svc.revoke_memory("memory:b", reason="误删")
    assert "memory:b" not in [it["entry_id"] for it in svc.recall("删", top_k=5, rebuild_if_missing=False)["items"]]

    assert svc.restore_memory("memory:b", reason="误删恢复") is True
    ids = [it["entry_id"] for it in svc.recall("删", top_k=5, rebuild_if_missing=False)["items"]]
    assert "memory:b" in ids, "恢复后必须重新可召回"
    state = svc.data_store.load_json("assistant_memory_index", {"entries": []})
    b = next(e for e in state["entries"] if e["entry_id"] == "memory:b")
    meta = b.get("metadata") or {}
    assert "revoked" not in meta and meta.get("restored_at"), "revoked 标记族须清除并留恢复痕"


def test_restore_sets_pending_sync_and_engine_reincludes(tmp_path):
    """恢复必须触发引擎重同步，且该条目重新进入 sync 列表（否则 JSON 恢复了、
    hybrid 召回仍缺 —— 与 U3 刚修的软删半截对称的双状态面）。"""
    engine = _RecordingEngine()
    svc, _ = _make_index(tmp_path, memory_search=engine)
    _seed(svc, [_entry("memory:a", "保留"), _entry("memory:b", "恢复测试")])
    svc.revoke_memory("memory:b")
    svc._sync_memory_search()
    assert "memory:b" not in [e.get("entry_id") for e in engine.synced[-1]]

    svc.restore_memory("memory:b")
    assert svc._pending_sync is True, "restore 后必须置 pending_sync"
    svc._sync_memory_search()
    assert "memory:b" in [e.get("entry_id") for e in engine.synced[-1]], "恢复条目须重新同步进引擎"


def test_restore_rejects_unknown_and_never_revoked(tmp_path):
    svc, _ = _make_index(tmp_path)
    _seed(svc, [_entry("memory:a", "从未撤销")])
    assert svc.restore_memory("memory:ghost") is False
    assert svc.restore_memory("memory:a") is False, "未 revoked 的条目恢复应返回 False，不得改写时间戳"
    state = svc.data_store.load_json("assistant_memory_index", {"entries": []})
    a = next(e for e in state["entries"] if e["entry_id"] == "memory:a")
    assert a["updated_at"] == "2026-09-16T00:00:00+00:00"
    assert not (a.get("metadata") or {}).get("restored_at")


def test_restore_survives_rebuild(tmp_path):
    """rebuild_index 继承 revoked 键集 —— 已恢复的条目不得被重新打回软删。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    notes = [{"id": "1", "title": "笔记甲", "content": "甲内容", "updated_at": "2026-09-16T00:00:00"}]
    svc = AssistantMemoryIndexService(data_store=store, note_repository=_FakeNoteRepo(notes))
    svc.rebuild_index()
    svc.revoke_memory("note:1")
    svc.restore_memory("note:1")
    svc.rebuild_index()  # 旧 revoked 集合此刻应为空
    state = svc.data_store.load_json("assistant_memory_index", {"entries": []})
    n1 = next(e for e in state["entries"] if e["entry_id"] == "note:1")
    assert not (n1.get("metadata") or {}).get("revoked"), "恢复后 rebuild 不得复活软删标记"
    assert "note:1" in [it["entry_id"] for it in svc.recall("笔记", top_k=5, rebuild_if_missing=False)["items"]]


class _FakeNoteRepo:
    def __init__(self, notes):
        self._notes = notes

    def list_notes(self, include_deleted=False):
        return list(self._notes)


def test_restore_appends_audit_record(tmp_path):
    svc, _ = _make_index(tmp_path)
    _seed(svc, [_entry("memory:b", "审计")])
    svc.revoke_memory("memory:b")
    svc.restore_memory("memory:b", reason="恢复留痕")
    log = svc.data_store.load_json("assistant_memory_audit_log", [])
    actions = [(r.get("action"), r.get("entry_id")) for r in log]
    assert ("revoke", "memory:b") in actions
    assert ("restore", "memory:b") in actions, "恢复动作必须留审计痕（可逆性证据）"


# ══════════════════════════════════════
# 3. RPC：memory.list / memory.restore
# ══════════════════════════════════════


def _reg(svc):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    ctx = SimpleNamespace(memory_index_service=svc)
    reg = MethodRegistry()
    register_chat_memory(reg, ctx)
    return reg


@pytest.mark.asyncio
async def test_rpc_memory_list_roundtrip_with_revoke_and_restore(tmp_path):
    svc, _ = _make_index(tmp_path)
    _seed(svc, [_entry("memory:a", "保留", "2026-09-10T00:00:00+00:00"), _entry("memory:b", "删它", "2026-09-16T00:00:00+00:00")])
    reg = _reg(svc)

    listed = await reg.dispatch("memory.list", {"limit": 50, "offset": 0})
    assert listed["success"] is True
    assert [it["entry_id"] for it in listed["items"]] == ["memory:b", "memory:a"]
    assert listed["total"] == 2 and listed["total_revoked"] == 0

    await reg.dispatch("memory.delete", {"entry_id": "memory:b", "reason": "面板删除"})
    active = await reg.dispatch("memory.list", {})
    assert [it["entry_id"] for it in active["items"]] == ["memory:a"]
    assert active["total_revoked"] == 1

    trashed = await reg.dispatch("memory.list", {"include_revoked": True})
    b = next(it for it in trashed["items"] if it["entry_id"] == "memory:b")
    assert b["metadata"]["revoked"] is True

    restored = await reg.dispatch("memory.restore", {"entry_id": "memory:b", "reason": "面板恢复"})
    assert restored["success"] is True and restored["mode"] == "restored"
    after = await reg.dispatch("memory.list", {})
    assert [it["entry_id"] for it in after["items"]] == ["memory:b", "memory:a"]
    assert after["total_revoked"] == 0


@pytest.mark.asyncio
async def test_rpc_memory_restore_param_guards(tmp_path):
    svc, _ = _make_index(tmp_path)
    _seed(svc, [_entry("memory:a", "从未撤销")])
    reg = _reg(svc)

    r = await reg.dispatch("memory.restore", {"entry_id": "  "})
    assert r["success"] is False and "entry_id" in r["error"]
    r2 = await reg.dispatch("memory.restore", {"entry_id": "memory:ghost"})
    assert r2["success"] is False and "ghost" in r2["error"]


@pytest.mark.asyncio
async def test_rpc_memory_list_and_restore_service_unavailable():
    ctx = SimpleNamespace(memory_index_service=None)
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    reg = MethodRegistry()
    register_chat_memory(reg, ctx)
    r = await reg.dispatch("memory.list", {})
    assert r["success"] is False and "记忆服务" in r["error"]
    r2 = await reg.dispatch("memory.restore", {"entry_id": "memory:a"})
    assert r2["success"] is False


@pytest.mark.asyncio
async def test_rpc_memory_list_rejects_non_numeric_limit(tmp_path):
    """dispatch 层被 jsonschema 先拦（-32602）；handler 自身对脏值也要诚实报错
    （legacy 直调路径）——这里直接调 handler 验证兜底。"""
    svc, _ = _make_index(tmp_path)
    _seed(svc, [_entry("memory:a", "x")])
    reg = _reg(svc)
    handler = reg._handlers["memory.list"]
    r = await handler(limit="not-a-number")
    assert r["success"] is False and "limit" in r["error"]
    # dispatch 走 schema：非 integer 直接 -32602，不落 handler
    from core.errors import InvalidParamsError

    with pytest.raises(InvalidParamsError):
        await reg.dispatch("memory.list", {"limit": "not-a-number"})


# ══════════════════════════════════════
# 4. 注册面与 schema（门禁口径）
# ══════════════════════════════════════


def test_both_new_methods_registered_with_schema(tmp_path):
    """新注册必须走 register_with_schema —— schema_coverage_ratchet 的硬要求。"""
    svc, _ = _make_index(tmp_path)
    from backend.handlers._chat_memory import register_chat_memory

    class _Recording:
        def __init__(self):
            self.plain: dict = {}
            self.with_schema: dict = {}

        def register(self, name, fn):
            self.plain[name] = fn

        def register_with_schema(self, name, fn, schema=None, **kw):
            self.with_schema[name] = schema

    rec = _Recording()
    register_chat_memory(rec, SimpleNamespace(memory_index_service=svc))
    for name in ("memory.list", "memory.restore"):
        assert name in rec.with_schema, f"{name} 未以 register_with_schema 注册"
        assert isinstance(rec.with_schema[name], dict) and rec.with_schema[name].get("type") == "object"
    assert "memory.list" not in rec.plain and "memory.restore" not in rec.plain
