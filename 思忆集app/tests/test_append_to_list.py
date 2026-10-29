"""T2.2 写放大治理回归测试：append_to_list 增量追加。

验证点：
1. 多次追加后读取结果与旧路径（load→append→save_json）语义等价；
2. cap 截断正确（保留最后 cap 条，顺序不变）；
3. 缓存一致性（load 后 append，再 load 立即可见新增）；
4. 写放大：append_to_list 的 per-op 序列化成本远低于 update_json 整 blob 重写。
"""
from __future__ import annotations

import json
import tempfile

from data.local_storage import LocalDataStore


def _new_store() -> LocalDataStore:
    return LocalDataStore(base_dir=tempfile.mkdtemp())


def test_append_equivalent_to_update_json_with_cap():
    new = _new_store()
    old = _new_store()
    cap = 5
    for i in range(12):
        new.append_to_list("k", {"n": i}, cap=cap)
        old.update_json("k", [], lambda c: (c + [{"n": i}])[-cap:])
    assert new.load_json("k", []) == old.load_json("k", [])
    assert new.load_json("k", []) == [{"n": i} for i in range(7, 12)]


def test_append_cap_truncation_keeps_last():
    s = _new_store()
    for i in range(7):
        s.append_to_list("k", {"i": i}, cap=5)
    assert s.load_json("k", []) == [{"i": i} for i in range(2, 7)]


def test_append_cache_consistency():
    s = _new_store()
    s.load_json("k", [])  # 预热缓存
    s.append_to_list("k", {"x": 1}, cap=5)
    assert s.load_json("k", [])[-1] == {"x": 1}


def test_append_empty_then_many():
    s = _new_store()
    s.append_to_list("k", {"a": 1}, cap=3)
    s.append_to_list("k", {"a": 2}, cap=3)
    assert s.load_json("k", []) == [{"a": 1}, {"a": 2}]


def test_write_amplification_reduction():
    """append_to_list 的 per-op 序列化字节应远低于 update_json 整 blob 重写。"""
    new = _new_store()
    old = _new_store()
    rec = {"ts": 1, "v": "x" * 64}
    rec_bytes = len(json.dumps(rec, ensure_ascii=False).encode("utf-8"))
    n, cap = 1000, 500

    class _Cnt:
        def __init__(self, real):
            self.bytes = 0
            self._real = real
        def __call__(self, *a, **k):
            s = self._real(*a, **k)
            self.bytes += len(s.encode("utf-8"))
            return s

    real = json.dumps
    ca, cb = _Cnt(real), _Cnt(real)
    json.dumps = ca  # type: ignore[assignment]
    try:
        for _ in range(n):
            old.update_json("k", [], lambda c: (c + [dict(rec)])[-cap:])
    finally:
        json.dumps = real
    json.dumps = cb  # type: ignore[assignment]
    try:
        for _ in range(n):
            new.append_to_list("j", dict(rec), cap=cap)
    finally:
        json.dumps = real

    # 新路径仅序列化单项，放大系数应 ≈1；旧路径放大系数 >>1
    amp_old = ca.bytes / (n * rec_bytes)
    amp_new = cb.bytes / (n * rec_bytes)
    assert amp_old > 10, f"旧路径放大异常低: {amp_old}"
    assert amp_new < 2, f"新路径放大应≈1，实际 {amp_new}"
    assert cb.bytes < ca.bytes * 0.5, "写放大未下降≥50%"
