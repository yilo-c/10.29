"""Task + Reminder 鏈€小冒烟回归：验证任务创建/编辑/完成/删除经仓鍌"提醒服务，且关闭重开后保鐣欍€"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from datetime import datetime, timezone

from data.local_storage import LocalDataStore
from repositories import (
    LocalPersonalTaskRepository,
    LocalReminderRepository,
)
from services.reminder import ReminderService


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- 任务 + 提醒閾"----------


def test_task_create_creates_reminder(tmp_path):
    """任务带提醒时间时，ReminderService 鑷姩创建对应提醒。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    task_repo = LocalPersonalTaskRepository(store)
    reminder_repo = LocalReminderRepository(store)
    reminder_svc = ReminderService(data_store=store, reminder_repository=reminder_repo)

    # 创建涓€涓甫提醒时间的任鍔"
    task_id = task_repo.add_task({
        "name": "带提醒的任务",
        "reminder_time": _now_iso(),
        "note": "",
    })

    # 鍚屾任务提醒
    tasks = task_repo.list_tasks()
    reminder_svc.sync_task_alarms(tasks)

    # 验证提醒已创寤"
    alarms = reminder_svc.list_alarms()
    task_alarms = [a for a in alarms if a.get("task_id") == task_id]
    assert len(task_alarms) == 1, f"期望 1 涓换务提醒，实际 {len(task_alarms)}: {alarms}"
    assert task_alarms[0]["task"] == "带提醒的任务"


def test_task_delete_removes_reminder(tmp_path):
    """删除任务后，对应提醒也应移除。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    task_repo = LocalPersonalTaskRepository(store)
    reminder_repo = LocalReminderRepository(store)
    reminder_svc = ReminderService(data_store=store, reminder_repository=reminder_repo)

    # 创建任务
    task_id = task_repo.add_task({
        "name": "待删除任鍔",
        "reminder_time": _now_iso(),
    })
    tasks = task_repo.list_tasks()
    reminder_svc.sync_task_alarms(tasks)

    # 验证提醒存在
    assert any(a.get("task_id") == task_id for a in reminder_svc.list_alarms())

    # 删除任务
    task_repo.delete_task(task_id)
    reminder_svc.remove_task_alarm(task_id)

    # 验证提醒宸茶移除
    assert not any(a.get("task_id") == task_id for a in reminder_svc.list_alarms())


def test_task_completion_removes_reminder(tmp_path):
    """完成任务后，对应提醒应移闄ゃ€"""
    store = LocalDataStore(base_dir=str(tmp_path))
    task_repo = LocalPersonalTaskRepository(store)
    reminder_repo = LocalReminderRepository(store)
    reminder_svc = ReminderService(data_store=store, reminder_repository=reminder_repo)

    task_id = task_repo.add_task({
        "name": "要完成的任务",
        "reminder_time": _now_iso(),
    })
    tasks = task_repo.list_tasks()
    reminder_svc.sync_task_alarms(tasks)
    assert any(a.get("task_id") == task_id for a in reminder_svc.list_alarms())

    # 完成任务
    task_repo.toggle_task_completion(task_id, True)
    reminder_svc.remove_task_alarm(task_id)

    # 验证提醒已移闄"
    assert not any(a.get("task_id") == task_id for a in reminder_svc.list_alarms())


# ---------- 关闭重开保留 ----------


def test_task_and_reminder_survive_close_reopen(tmp_path):
    """关闭后重寮€，任务数鎹拰提醒均保鐣欍€"""
    store = LocalDataStore(base_dir=str(tmp_path))
    task_repo = LocalPersonalTaskRepository(store)
    reminder_repo = LocalReminderRepository(store)

    # "第一轮"：创建任鍔"
    task_id = task_repo.add_task({
        "name": "持久化任鍔",
        "reminder_time": _now_iso(),
    })
    tasks = task_repo.list_tasks()
    reminder_repo.sync_task_alarms(tasks)

    # "关闭"：销毁所鏈夊象（模拟关闭应用锛"
    saved_task_id = task_id
    del task_repo
    del reminder_repo
    del store

    # "重开"：用相同鐩綍重新加载
    store2 = LocalDataStore(base_dir=str(tmp_path))
    task_repo2 = LocalPersonalTaskRepository(store2)
    reminder_repo2 = LocalReminderRepository(store2)

    # 验证任务保留
    reloaded_tasks = task_repo2.list_tasks()
    assert len(reloaded_tasks) >= 1
    reloaded = [t for t in reloaded_tasks if t.get("id") == saved_task_id]
    assert len(reloaded) == 1, f"期望保留任务 {saved_task_id}"
    assert reloaded[0]["name"] == "持久化任鍔"

    # 验证提醒保留
    reminder_svc2 = ReminderService(data_store=store2, reminder_repository=reminder_repo2)
    alarms = reminder_svc2.list_alarms()
    task_alarms = [a for a in alarms if a.get("task_id") == saved_task_id]
    assert len(task_alarms) == 1, f"期望保留提醒，实闄{len(task_alarms)}: {alarms}"
    assert task_alarms[0]["task"] == "持久化任鍔"


# ---------- ReminderRepository 涓ReminderService 协作 ----------


def test_reminder_repository_direct_persistence(tmp_path):
    """ReminderRepository 鍙洿鎺ヨ写提醒，不需经过 ReminderService。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    reminder_repo = LocalReminderRepository(store)

    # 直接通过仓库添加提醒
    reminder_repo.add_alarm({
        "time": _now_iso(),
        "repeat": "每天",
        "task": "鐙珛提醒",
        "task_id": 42,
    })

    # 验证
    alarms = reminder_repo.list_alarms()
    assert any(a.get("task_id") == 42 for a in alarms)

    # 移除
    reminder_repo.remove_task_alarm(42)
    assert not any(a.get("task_id") == 42 for a in reminder_repo.list_alarms())


def test_reminder_service_uses_repository(tmp_path):
    """ReminderService 配置浜reminder_repository 后，持久化走仓库灞傘€"""
    store = LocalDataStore(base_dir=str(tmp_path))
    reminder_repo = LocalReminderRepository(store)
    reminder_svc = ReminderService(data_store=store, reminder_repository=reminder_repo)

    reminder_svc.add_alarm({
        "time": _now_iso(),
        "repeat": "不重澶",
        "task": "服务添加的提閱",
        "task_id": 99,
    })

    # 验证数据通过仓库持久鍖"
    alarms = reminder_repo.list_alarms()
    assert any(a.get("task_id") == 99 for a in alarms)

    # 重开验证保留
    reminder_repo2 = LocalReminderRepository(store)
    alarms2 = reminder_repo2.list_alarms()
    assert any(a.get("task_id") == 99 for a in alarms2)
