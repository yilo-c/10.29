"""QUA-8 回归：repositories 层局部 RMW 必须走事务型 update_json。

覆盖三件事：
1. 语义等价——改成 update_json 后，增/改/删单条的结果与旧 load→save 一致；
2. 并发不丢更新——旧写法是「整值覆盖」，两线程各加一条必然后者抹掉前者；
3. 事务性——updater 抛错时回滚，键值保持写入前原样（不会留下中间态）。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore  # noqa: E402
from repositories._inbox import LocalPersonalInboxRepository  # noqa: E402
from repositories._info_item import LocalInfoItemRepository  # noqa: E402
from repositories._note import LocalPersonalNoteRepository  # noqa: E402
from repositories._proposal import LocalImprovementProposalRepository  # noqa: E402
from repositories._reminder import LocalReminderRepository  # noqa: E402
from repositories._task import LocalPersonalTaskRepository  # noqa: E402


def _run_threads(worker: callable, count: int) -> None:
    errors: list[BaseException] = []

    def _guard(index: int) -> None:
        try:
            worker(index)
        except BaseException as exc:  # noqa: BLE001 - 收集后在主线程重抛
            errors.append(exc)

    threads = [threading.Thread(target=_guard, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


# ── info_items.upsert_items ─────────────────────────────────────────────


def test_info_item_upsert_insert_then_merge(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalInfoItemRepository(store)

    assert repo.upsert_items([{"url": "https://a", "title": "A", "score": 1}]) == 1
    assert repo.upsert_items([{"url": "https://a", "summary": "s", "read": True}]) == 1

    items, total = repo.list_items(limit=10)
    assert total == 1
    assert items[0]["title"] == "A"
    assert items[0]["summary"] == "s"
    assert items[0]["read"] is False, "read/saved 不在 _CONTENT_FIELDS 中，不应被 upsert 覆写"
    assert items[0]["updated_at"] >= items[0]["created_at"]
    store.close()


def test_info_item_upsert_concurrent_keeps_every_record(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalInfoItemRepository(store)

    _run_threads(
        lambda i: repo.upsert_items([{"url": f"https://u/{i}/{j}", "title": f"t{j}"} for j in range(5)]),
        8,
    )

    _, total = repo.list_items(limit=100)
    assert total == 40, "并发 upsert 出现整值覆盖丢写"
    store.close()


def test_info_item_upsert_skips_records_without_url(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalInfoItemRepository(store)
    assert repo.upsert_items([{"title": "no url"}, {"url": "  "}]) == 0
    assert repo.list_items()[1] == 0
    store.close()


# ── inbox_entries ───────────────────────────────────────────────────────


def test_inbox_add_entry_ids_and_defaults(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalInboxRepository(store)

    first = repo.add_entry({"title": "a", "tags": [" x ", "", "y"]})
    second = repo.add_entry({"content": "b"})
    assert first == 1 and second == 2

    entries = repo.list_entries()
    assert [e["id"] for e in entries] == [1, 2]
    assert entries[0]["tags"] == ["x", "y"]
    assert entries[0]["source"] == "manual"
    assert entries[1]["title"] == "未命名收件"
    store.close()


def test_inbox_add_entry_concurrent_never_reuses_id(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalInboxRepository(store)

    ids: list[int] = []
    lock = threading.Lock()

    def _worker(index: int) -> None:
        new_id = repo.add_entry({"title": f"t{index}"})
        with lock:
            ids.append(new_id)

    _run_threads(_worker, 10)

    assert sorted(ids) == list(range(1, 11)), "并发 add_entry 分配到重复 id"
    assert len(repo.list_entries(include_archived=True)) == 10
    store.close()


def test_inbox_update_entry_touches_only_target(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalInboxRepository(store)
    a = repo.add_entry({"title": "a"})
    b = repo.add_entry({"title": "b"})

    repo.update_entry(a, {"status": "archived", "tags": [" p ", ""]})

    entries = {e["id"]: e for e in repo.list_entries(include_archived=True)}
    assert entries[a]["status"] == "archived"
    assert entries[a]["tags"] == ["p"]
    assert entries[b]["status"] == "new"
    assert repo.list_entries() == [entries[b]]
    store.close()


def test_inbox_rolls_back_when_updater_fails(tmp_path, monkeypatch):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalInboxRepository(store)
    entry_id = repo.add_entry({"title": "keep"})
    before = store.load_json("inbox_entries", [])

    def _boom(_entries):
        raise RuntimeError("updater 内部失败")

    monkeypatch.setattr(store, "next_id", _boom)
    with pytest.raises(RuntimeError):
        repo.add_entry({"title": "should-not-persist"})

    assert store.load_json("inbox_entries", []) == before
    assert [e["title"] for e in repo.list_entries()] == ["keep"]
    assert entry_id == 1
    store.close()


# ── improvement_proposals ───────────────────────────────────────────────


def test_proposal_add_and_update(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalImprovementProposalRepository(store)

    pid = repo.add_proposal({"title": "p"})
    other = repo.add_proposal({})
    assert (pid, other) == (1, 2)

    repo.update_proposal(pid, {"status": "approved"})
    items = {p["id"]: p for p in repo.list_proposals()}
    assert items[pid]["status"] == "approved"
    assert items[other]["status"] == "pending_review"
    assert items[other]["title"] == "未命名提案"
    store.close()


def test_proposal_add_concurrent_keeps_all(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalImprovementProposalRepository(store)

    _run_threads(lambda i: repo.add_proposal({"title": f"p{i}"}), 8)
    assert len(repo.list_proposals()) == 8
    store.close()


# ── reminders ───────────────────────────────────────────────────────────


def test_reminder_add_and_remove_task_alarm(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalReminderRepository(store)

    repo.add_alarm({"time": "2026-01-01 08:00", "task": "keep", "task_id": 1})
    repo.add_alarm({"time": "2026-02-02 09:00", "task": "drop", "task_id": 2})
    repo.remove_task_alarm(2)
    repo.remove_task_alarm(None)

    alarms = repo.list_alarms()
    assert [a["task_id"] for a in alarms] == [1]
    assert alarms[0]["task"] == "keep"
    assert str(store.load_json("reminders", [])).count("2026-01-01 08:00") == 1
    store.close()


def test_reminder_add_alarm_concurrent_keeps_all(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalReminderRepository(store)

    _run_threads(lambda i: repo.add_alarm({"time": "2026-01-01 08:00", "task_id": i}), 8)
    assert sorted(a["task_id"] for a in repo.list_alarms()) == list(range(8))
    store.close()


# ── folders / tasks ─────────────────────────────────────────────────────


def test_note_add_folder_is_idempotent_and_seeds_root(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalNoteRepository(store)

    repo.add_folder("/工作")
    repo.add_folder("/工作")
    repo.add_folder("/生活")

    assert store.load_json("folders", []) == ["/", "/工作", "/生活"]
    assert repo.get_folders() == ["/", "/工作", "/生活"]
    store.close()


def test_task_mark_as_synced_and_resolve_local_only_deleted(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalTaskRepository(store)
    store.save_json("tasks", [
        {"id": 1, "name": "a", "sync_state": "dirty", "version": 3},
        {"id": 2, "name": "b", "sync_state": "local_only", "deleted_at": "2026-01-01"},
    ])

    repo.mark_task_as_synced(1, {"remote_id": " r1 ", "version": 5, "last_synced_at": "t"})
    tasks = {t["id"]: t for t in store.load_json("tasks", [])}
    assert tasks[1]["sync_state"] == "synced"
    assert tasks[1]["remote_id"] == "r1"
    assert tasks[1]["version"] == 5
    assert tasks[2]["sync_state"] == "local_only"

    repo.mark_task_as_synced(99, {"remote_id": "nope"})
    assert len(store.load_json("tasks", [])) == 2

    repo.resolve_local_only_deleted_task(2)
    assert [t["id"] for t in store.load_json("tasks", [])] == [1]
    store.close()


def test_repositories_no_longer_use_bare_rmw_for_converted_paths(tmp_path):
    """已改造的调用点不得再出现 load_json→save_json 整值覆盖。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalInboxRepository(store)

    calls: list[str] = []
    real_save = store.save_json
    real_update = store.update_json

    def _spy_save(key, data):
        calls.append(f"save:{key}")
        return real_save(key, data)

    def _spy_update(key, default, updater):
        calls.append(f"update:{key}")
        return real_update(key, default, updater)

    store.save_json = _spy_save
    store.update_json = _spy_update

    repo.add_entry({"title": "x"})
    repo.update_entry(1, {"status": "archived"})

    assert calls == ["update:inbox_entries", "update:inbox_entries"], calls
    store.close()
