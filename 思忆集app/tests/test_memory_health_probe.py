"""system.memory_health cron 动作健康探针回归（2026-09-13）。

背景：E2E 旅程测试发现 cron_actions 的记忆健康巡检 handler 调用
``LocalDataStore.health_check()``，但该方法不存在 → 每日 04:00 巡检必失败
（P0-4 连续失败告警链路被噪音淹没）。本文件锁定：
- LocalDataStore.health_check 三段探针全 ok 且可重复调用（线程共享连接不被破坏）；
- cron action 注册表里的 system.memory_health handler 用真实 LocalDataStore
  全链执行返回 status=completed。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.local_storage import LocalDataStore  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path) -> LocalDataStore:
    return LocalDataStore(base_dir=str(tmp_path / "hc_data"))


def test_health_check_ok_and_repeatable(store: LocalDataStore):
    r1 = store.health_check()
    assert r1["status"] == "ok", r1
    assert r1["checks"]["sqlite_queryable"] is True
    assert r1["checks"]["integrity"] is True
    assert r1["checks"]["kv_roundtrip"] is True
    r2 = store.health_check()  # 线程共享连接未被第一次调用破坏
    assert r2["status"] == "ok"


def test_health_check_after_real_writes(store: LocalDataStore):
    store.save_json("some_key", [{"a": 1}])
    assert store.health_check()["status"] == "ok"


def test_cron_action_memory_health_end_to_end(store: LocalDataStore):
    from services.cron_actions import _REGISTRY

    spec = _REGISTRY.get("system.memory_health")
    assert spec is not None, "system.memory_health 未注册"
    ctx = SimpleNamespace(local_data_store=store, memory_index_service=None)
    result = spec.handler(ctx, {})
    assert result["status"] == "completed", result
    assert result["db_health"]["status"] == "ok"
    assert result["memory_health"]["status"] == "skipped"  # 无记忆索引服务 → 跳过语义
