"""D22-1 止血①回归：budget_manager._persist_record 改接 append_to_list。

验证点（任务书出口断言）：
1. 经 record_usage 追加 N=3 条事件后，SQLite 整值重写路径（_sqlite.update）
   调用计数 == 0（写放大已消除）；
2. append_to_list(cap=10000) 后读回长度 == 3；
3. cap=5 时只保留最后 5 条（与旧 [-10000:] 截尾语义等价的截尾行为验证）。
"""
from __future__ import annotations

import tempfile

from assistant.budget_manager import BudgetManager
from data.local_storage import LocalDataStore


def _new_store() -> LocalDataStore:
    return LocalDataStore(base_dir=tempfile.mkdtemp())


def test_persist_avoids_full_blob_update_and_keeps_count():
    store = _new_store()
    calls = {"update": 0}
    real_update = store._sqlite.update

    def _counting_update(*args, **kwargs):
        calls["update"] += 1
        return real_update(*args, **kwargs)

    store._sqlite.update = _counting_update  # type: ignore[method-assign]

    mgr = BudgetManager(data_store=store)
    for i in range(3):
        mgr.record_usage(tokens=100 + i, cost_usd=0.01, api_calls=1, model="m", task_id=f"t{i}")

    assert calls["update"] == 0, "止血后不应再走 update_json 整值重写路径"
    items = store.load_json("assistant_budget_usage", [])
    assert len(items) == 3
    assert [it["task_id"] for it in items] == ["t0", "t1", "t2"]


def test_persist_cap_semantics_keeps_tail():
    store = _new_store()
    for i in range(7):
        store.append_to_list("assistant_budget_usage", {"task_id": f"t{i}"}, cap=5)
    items = store.load_json("assistant_budget_usage", [])
    assert len(items) == 5
    assert [it["task_id"] for it in items] == ["t2", "t3", "t4", "t5", "t6"]
