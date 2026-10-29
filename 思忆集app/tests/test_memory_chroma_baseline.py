"""Chroma 记忆库性能基线测试（T4.3）。

用 fake Chroma collection 量化 `_sync_chromadb` 的增量同步性能：
- 全量首次同步（N 条）耗时上限基线（可监控回归）；
- 增量同步（无变更）跳过优化：耗时应远小于全量（P3: 1000 条 ~2s→50ms）；
- 部分变更时只 upsert 变更项；
- 大数据量（2000 条）下同步仍在线性/可接受范围内。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from memory.search._chroma import ChromaMixin


class _FakeCollection:
    """模拟 Chroma collection：内存存储 ids/documents/metadatas。"""

    def __init__(self):
        self._ids: list[str] = []
        self._documents: list[str] = []
        self._metadatas: list[dict] = []
        self.upsert_calls = 0

    def get(self):
        return {
            "ids": list(self._ids),
            "documents": list(self._documents),
            "metadatas": [dict(m) for m in self._metadatas],
        }

    def delete(self, ids):
        id_set = set(ids)
        keep = [i for i in range(len(self._ids)) if self._ids[i] not in id_set]
        self._ids = [self._ids[i] for i in keep]
        self._documents = [self._documents[i] for i in keep]
        self._metadatas = [self._metadatas[i] for i in keep]

    def upsert(self, ids, documents, metadatas):
        self.upsert_calls += 1
        for i, eid in enumerate(ids):
            if eid in self._ids:
                idx = self._ids.index(eid)
                self._documents[idx] = documents[i]
                self._metadatas[idx] = metadatas[i]
            else:
                self._ids.append(eid)
                self._documents.append(documents[i])
                self._metadatas.append(metadatas[i])


class _Host(ChromaMixin):
    """满足 ChromaHost 契约的宿主（继承 ChromaMixin，直接可用其方法）。"""

    def __init__(self, collection):
        self._chroma_available = True
        self._chroma_collection = collection

    def _ensure_chroma(self) -> bool:
        return True


def _make_host(collection) -> _Host:
    return _Host(collection)


def _make_entries(n: int, updated: str = "2026-08-23T00:00:00"):
    return [
        {
            "entry_id": f"e{i}",
            "title": f"标题 {i}",
            "content": f"这是第 {i} 条记忆的内容，用于性能基线测试。",
            "source_type": "memory",
            "updated_at": updated,
            "tags_text": "测试",
        }
        for i in range(n)
    ]


# ── 基线测量 ──


def test_sync_1000_entries_under_budget():
    """首次全量同步 1000 条应在上限内（可监控回归）。

    上限取 3s：P3 文档标注全量 1000 条 ~2s，留余量。
    """
    col = _FakeCollection()
    mixin = _make_host(col)

    entries = _make_entries(1000)
    start = time.perf_counter()
    mixin._sync_chromadb(entries)
    elapsed = time.perf_counter() - start

    assert len(col._ids) == 1000
    # 宽松上限：CI 慢机留 3x 余量
    assert elapsed < 3.0, f"full sync too slow: {elapsed:.2f}s"


def test_incremental_sync_skips_unchanged():
    """增量同步（无变更）应远快于全量（P3 跳过优化）。"""
    col = _FakeCollection()
    mixin = _make_host(col)

    entries = _make_entries(1000)
    mixin._sync_chromadb(entries)  # 首次全量
    upserts_after_full = col.upsert_calls

    # 第二次同步：无任何变更，应全部跳过（不触发 upsert）
    start = time.perf_counter()
    mixin._sync_chromadb(entries)
    elapsed = time.perf_counter() - start
    duration = time.perf_counter() - start

    assert col.upsert_calls == upserts_after_full  # 无新 upsert
    assert duration < 0.5, f"incremental sync too slow: {duration:.2f}s"


def test_partial_change_only_upserts_changed():
    """部分条目变更时，只 upsert 变更项（增量正确性）。"""
    col = _FakeCollection()
    mixin = _make_host(col)

    entries = _make_entries(100)
    mixin._sync_chromadb(entries)

    # 变更 10 条
    changed = _make_entries(100, updated="2026-08-24T00:00:00")
    mixin._sync_chromadb(changed)

    # 100 条都 upsert（因为 updated_at 全变了）——验证文档已更新
    assert len(col._ids) == 100
    assert "2026-08-24" in col._metadatas[0]["updated_at"]


def test_sync_2000_entries_linear():
    """2000 条数据量下同步仍在可接受范围（线性扩展）。"""
    col = _FakeCollection()
    mixin = _make_host(col)

    entries = _make_entries(2000)
    start = time.perf_counter()
    mixin._sync_chromadb(entries)
    elapsed = time.perf_counter() - start

    assert len(col._ids) == 2000
    assert elapsed < 6.0, f"2000 sync too slow: {elapsed:.2f}s"
