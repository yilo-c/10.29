# -*- coding: utf-8 -*-
"""跨设备同步存储层 (api.db.sync_store) 单元测试。

P4.2-P0a/P0b：
* 验证 envelopes/devices/cursor/conflicts 的 CRUD 行为
* 验证 ``allocate_next_cursor`` 在并发推送下严格单调
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import pytest


@pytest.fixture
def isolated_db(monkeypatch, tmp_path: Path):
    """为每个测试创建一个独立 SQLite 数据库 + 重建会话工厂。"""
    db_path = tmp_path / "sync_test.sqlite3"

    from automation_hub.api.config import settings as api_settings
    monkeypatch.setattr(api_settings, "DATABASE_PATH", db_path, raising=True)

    from automation_hub.api.db import session as session_mod
    session_mod._reset_session_factory()

    # 通过 ORM 模型在新 DB 上建表
    from automation_hub.api.models import Base  # noqa: F401  # 触发全部模型导入
    from automation_hub.api.models.engine import create_engine_from_db_path

    engine, _ = create_engine_from_db_path(str(db_path))
    Base.metadata.create_all(bind=engine)
    engine.dispose()

    yield db_path

    session_mod._reset_session_factory()


def test_allocate_cursor_monotonic(isolated_db):
    from automation_hub.api.db import sync_store
    from automation_hub.api.db.session import get_db_session

    seen: list[int] = []
    with get_db_session() as session:
        for _ in range(5):
            seen.append(sync_store.allocate_next_cursor(session))
    assert seen == [1, 2, 3, 4, 5]


def test_allocate_cursor_batch(isolated_db):
    from automation_hub.api.db import sync_store
    from automation_hub.api.db.session import get_db_session

    with get_db_session() as session:
        first = sync_store.allocate_next_cursor(session, count=4)
        assert first == 1
        # 之后再分配，应当从 5 开始
        next_first = sync_store.allocate_next_cursor(session, count=2)
        assert next_first == 5
        assert sync_store.get_global_cursor(session) == 6


def test_insert_and_query_envelopes(isolated_db):
    from automation_hub.api.db import sync_store
    from automation_hub.api.db.session import get_db_session

    with get_db_session() as session:
        for idx in range(3):
            cursor = sync_store.allocate_next_cursor(session)
            sync_store.insert_envelope(
                session,
                envelope_id=f"env-{idx}",
                device_id="phone-1",
                resource_type="note" if idx < 2 else "task",
                resource_id=f"res-{idx}",
                action="create",
                payload={"text": f"n{idx}"},
                version=1,
                cursor=cursor,
                created_at="2026-06-23T00:00:00Z",
                checksum=f"chk-{idx}",
            )

    with get_db_session() as session:
        # since=0 应返回全部 3 个，按 cursor 升序
        envs, has_more = sync_store.query_envelopes(session, since=0, limit=100)
        assert [e["envelope_id"] for e in envs] == ["env-0", "env-1", "env-2"]
        assert has_more is False

        # resource_type 过滤
        notes, _ = sync_store.query_envelopes(session, since=0, resource_type="note")
        assert [e["envelope_id"] for e in notes] == ["env-0", "env-1"]

        # since 增量
        tail, _ = sync_store.query_envelopes(session, since=1)
        assert [e["envelope_id"] for e in tail] == ["env-1", "env-2"]


def test_query_envelopes_has_more_flag(isolated_db):
    from automation_hub.api.db import sync_store
    from automation_hub.api.db.session import get_db_session

    with get_db_session() as session:
        for idx in range(5):
            cursor = sync_store.allocate_next_cursor(session)
            sync_store.insert_envelope(
                session,
                envelope_id=f"env-{idx}",
                device_id="phone-1",
                resource_type="note",
                resource_id=f"r{idx}",
                action="create",
                payload={"i": idx},
                version=1,
                cursor=cursor,
            )

    with get_db_session() as session:
        page, has_more = sync_store.query_envelopes(session, since=0, limit=3)
        assert len(page) == 3
        assert has_more is True
        # limit 钳制到 [1, 200]
        big, _ = sync_store.query_envelopes(session, since=0, limit=9999)
        assert len(big) == 5


def test_bulk_insert_envelopes(isolated_db):
    from automation_hub.api.db import sync_store
    from automation_hub.api.db.session import get_db_session

    with get_db_session() as session:
        records = sync_store.bulk_insert_envelopes(
            session,
            [
                {"envelope_id": "a", "device_id": "d", "resource_type": "n", "resource_id": "1", "action": "create"},
                {"envelope_id": "b", "device_id": "d", "resource_type": "n", "resource_id": "2", "action": "create"},
            ],
        )
        assert [r.cursor for r in records] == [1, 2]

    with get_db_session() as session:
        assert sync_store.get_max_envelope_cursor(session) == 2


def test_upsert_device_inserts_then_updates(isolated_db):
    from automation_hub.api.db import sync_store
    from automation_hub.api.db.session import get_db_session

    with get_db_session() as session:
        sync_store.upsert_device(
            session,
            device_id="phone-A",
            status="online",
            last_seen_at="2026-06-23T01:00:00Z",
            extra={"name": "iPhone", "type": "ios"},
        )

    with get_db_session() as session:
        devices = sync_store.list_devices(session)
        assert len(devices) == 1
        assert devices[0]["device_id"] == "phone-A"
        assert devices[0]["status"] == "online"

        # update 同设备
        sync_store.upsert_device(
            session,
            device_id="phone-A",
            status="away",
            last_seen_at="2026-06-23T02:00:00Z",
            extra={"name": "iPhone-renamed"},
        )

    with get_db_session() as session:
        devices = sync_store.list_devices(session)
        assert len(devices) == 1
        assert devices[0]["status"] == "away"
        assert devices[0]["extra"]["name"] == "iPhone-renamed"


def test_conflict_insert_and_filter_by_device(isolated_db):
    from automation_hub.api.db import sync_store
    from automation_hub.api.db.session import get_db_session

    with get_db_session() as session:
        sync_store.insert_conflict(
            session,
            conflict_id="c-1",
            device_id="phone-A",
            entity_type="note",
            entity_id="n1",
            reason="version mismatch",
            resolution="server_wins",
            local_version=1,
            remote_version=2,
        )
        sync_store.insert_conflict(
            session,
            conflict_id="c-2",
            device_id="phone-B",
            entity_type="task",
            entity_id="t1",
            reason="dup",
        )

    with get_db_session() as session:
        a = sync_store.list_conflicts_for_device(session, "phone-A")
        b = sync_store.list_conflicts_for_device(session, "phone-B")
        none = sync_store.list_conflicts_for_device(session, "")
    assert [c["conflict_id"] for c in a] == ["c-1"]
    assert [c["conflict_id"] for c in b] == ["c-2"]
    assert none == []


def test_concurrent_cursor_allocation_is_monotonic(isolated_db):
    """模拟两个线程同时调用 ``allocate_next_cursor`` 不出现重号。"""
    from automation_hub.api.db import sync_store
    from automation_hub.api.db.session import get_db_session

    results: list[int] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        local: list[int] = []
        for _ in range(n):
            with get_db_session() as session:
                local.append(sync_store.allocate_next_cursor(session))
        with lock:
            results.extend(local)

    threads = [threading.Thread(target=worker, args=(20,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 总数正确 + 无重号 + 范围连续
    assert len(results) == 80
    assert len(set(results)) == 80
    assert sorted(results) == list(range(1, 81))


def test_pull_round_trip_through_routes_layer(isolated_db):
    """通过 sync_store 写入信封后，模拟 /sync/pull 行为读取。"""
    from automation_hub.api.db import sync_store
    from automation_hub.api.db.session import get_db_session

    with get_db_session() as session:
        cur = sync_store.allocate_next_cursor(session)
        sync_store.insert_envelope(
            session,
            envelope_id="rt-1",
            device_id="phone-A",
            resource_type="memo",
            resource_id="m1",
            action="create",
            payload={"hello": "world"},
            version=1,
            cursor=cur,
        )

    with get_db_session() as session:
        envs, has_more = sync_store.query_envelopes(session, since=0)
    assert has_more is False
    assert envs[0]["payload"] == {"hello": "world"}
    assert envs[0]["cursor"] == 1
    assert "received_at" in envs[0]
