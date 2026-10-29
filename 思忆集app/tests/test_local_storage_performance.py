"""LocalDataStore 性能基准测试（P1-6 评估配套）。

不追求绝对值——阈值故意宽松（实测 5–10×），目的是回归保护：
当未来某次重构意外把缓存绕过或写盘 IO 复杂度抬到 O(n²)，CI 会先红。

测量项：
1. ``load_json`` 缓存命中 — 1000 次平均 < 1 ms。
2. 1000 条记录全量 ``save_json``（含 .bak 轮转与写后校验）< 1500 ms。
3. 1000 条记录 ``_load_json_unlocked`` 单次 < 200 ms。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore


def _make_records(n: int) -> list[dict]:
    return [
        {
            "id": i,
            "title": f"item-{i}",
            "content": "x" * 200,  # 模拟 ~200B 文本字段
            "tags": ["benchmark", "p1-6"],
            "created_at": 1718000000 + i,
        }
        for i in range(n)
    ]


def test_load_json_cache_hit_is_constant_time(tmp_path):
    """缓存命中应稳定在 O(1)，1000 次累计 < 1 s（人眼级延迟）。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("tasks", _make_records(1000))
    # 预热：第一次 load 把数据填进缓存
    store.load_json("tasks", [])

    start = time.perf_counter()
    for _ in range(1000):
        store.load_json("tasks", [])
    elapsed = time.perf_counter() - start

    # 平均 < 1 ms / 次；阈值放到 1 s / 1000 次 = 1 ms 上限
    assert elapsed < 1.0, f"缓存命中 1000 次耗时 {elapsed:.3f}s，超过 1s 阈值"


def test_save_json_1000_records_within_budget(tmp_path):
    """1000 条全量写盘（含 .bak 轮转 + 写后校验）< 1.5 s。

    实测约 100–250 ms；阈值故意放宽 5–10× 防 CI 抖动。
    """
    store = LocalDataStore(base_dir=str(tmp_path))
    records = _make_records(1000)

    start = time.perf_counter()
    store.save_json("tasks", records)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.5, f"save_json(1000 条) 耗时 {elapsed:.3f}s，超过 1.5s 阈值"


def test_load_json_unlocked_1000_records_within_budget(tmp_path):
    """绕过缓存的纯解析路径 < 200 ms（1000 条 ~200KB JSON）。

    P1-6 迁移后底层是 SQLite，没有 ``_load_json_unlocked`` 私有方法。
    用 ``invalidate_cache`` + ``load_json`` 模拟「缓存未命中」的解析开销。
    """
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("tasks", _make_records(1000))
    store.invalidate_cache("tasks")

    start = time.perf_counter()
    data = store.load_json("tasks", [])
    elapsed = time.perf_counter() - start

    assert len(data) == 1000
    assert elapsed < 0.2, f"load_json(cache miss, 1000 条) 耗时 {elapsed:.3f}s，超过 200ms 阈值"


def test_save_json_invalidates_cache(tmp_path):
    """save_json 必须清掉同路径缓存，否则后续 load 拿到旧数据。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("tasks", [{"id": 1}])
    assert store.load_json("tasks", []) == [{"id": 1}]

    store.save_json("tasks", [{"id": 2}])
    # 缓存应被清空，下一次 load 重新读盘
    assert store.load_json("tasks", []) == [{"id": 2}]


def test_lru_eviction_under_cache_pressure(tmp_path):
    """LRU 容量上限超出后应淘汰最久未用条目。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    # 人为缩小缓存上限，避开真实 128 阈值
    store._CACHE_MAX_SIZE = 3

    for key in ("tasks", "notes", "wordbooks", "translations"):
        store.save_json(key, [{"key": key}])
        store.load_json(key, [])

    # 最早访问的 tasks 应被淘汰
    assert "tasks" not in store._cache
    # 最近三个仍在缓存里
    assert {"notes", "wordbooks", "translations"} <= set(store._cache.keys())
