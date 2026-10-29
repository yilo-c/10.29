"""ReminderService 单元测试：CRUD 与任务提醒联鍔ㄣ€"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest
from services.reminder import ReminderService


class _MockRepo:
    def __init__(self):
        self.alarms = []

    def list_alarms(self):
        return list(self.alarms)

    def replace_alarms(self, alarms):
        self.alarms = list(alarms)

    def add_alarm(self, alarm_info):
        self.alarms.append(dict(alarm_info))

    def remove_task_alarm(self, task_id):
        self.alarms = [a for a in self.alarms if a.get("task_id") != task_id]


# 鈹€鈹€ Fixtures 鈹€鈹€


@pytest.fixture
def mock_repo():
    return _MockRepo()


@pytest.fixture
def service_with_repo(mock_repo):
    return ReminderService(reminder_repository=mock_repo)


@pytest.fixture
def service_with_store(tmp_path):
    """使用 LocalDataStore 鐨"ReminderService。"""
    from data.local_storage import LocalDataStore

    store = LocalDataStore(base_dir=str(tmp_path))
    return ReminderService(data_store=store)


# ══════════════════════════════════════════════════════════鈺"# 基本 CRUD
# ══════════════════════════════════════════════════════════鈺"

class TestBasicCRUD:
    def test_add_and_list_alarms(self, service_with_repo, mock_repo):
        """添加提醒鍚"list_alarms 应包鍚柊条目。"""
        service_with_repo.add_alarm({
            "time": "2026-05-07 10:00",
            "repeat": "每天",
            "task": "测试提醒",
            "task_id": 1,
        })
        alarms = service_with_repo.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["task"] == "测试提醒"
        assert alarms[0]["repeat"] == "每天"

    def test_add_multiple_alarms(self, service_with_repo):
        """澶氭添加应累鍔犮€"""
        for i in range(3):
            service_with_repo.add_alarm({"time": f"10:{i:02d}", "task": f"任务{i}", "task_id": i})
        assert len(service_with_repo.list_alarms()) == 3

    def test_replace_alarms(self, service_with_repo):
        """replace_alarms 应替换全部提閱掋€"""
        service_with_repo.add_alarm({"time": "10:00", "task": "旧任鍔", "task_id": 1})
        service_with_repo.replace_alarms([
            {"time": "14:00", "task": "新任鍔", "task_id": 2},
        ])
        alarms = service_with_repo.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["task"] == "新任鍔"

    def test_initial_empty_alarms(self, service_with_repo):
        """新建鐨"ReminderService 应无提醒。"""
        assert service_with_repo.list_alarms() == []

    def test_remove_task_alarm(self, service_with_repo):
        """移除特定 task_id 的提閱掋€"""
        service_with_repo.add_alarm({"time": "10:00", "task": "A", "task_id": 1})
        service_with_repo.add_alarm({"time": "11:00", "task": "B", "task_id": 2})
        service_with_repo.remove_task_alarm(1)
        alarms = service_with_repo.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["task_id"] == 2

    def test_remove_task_alarm_nonexistent(self, service_with_repo):
        """移除不存在的 task_id 不报閿欍€"""
        service_with_repo.add_alarm({"time": "10:00", "task": "A", "task_id": 1})
        service_with_repo.remove_task_alarm(999)
        assert len(service_with_repo.list_alarms()) == 1

    def test_remove_task_alarm_none(self, service_with_repo):
        """task_id 一"None 时应无操浣溿€"""
        service_with_repo.add_alarm({"time": "10:00", "task": "A", "task_id": 1})
        service_with_repo.remove_task_alarm(None)
        assert len(service_with_repo.list_alarms()) == 1


# ══════════════════════════════════════════════════════════鈺"# 任务提醒联动
# ══════════════════════════════════════════════════════════鈺"

class TestTaskAlarmSync:
    def test_sync_task_alarms_creates_alarms(self, service_with_repo):
        """sync_task_alarms 应为带提醒时间的任务创建提醒。"""
        tasks = [
            {"id": 1, "name": "任务1", "reminder_time": "2026-05-07 14:00"},
            {"id": 2, "name": "任务2", "reminder_time": ""},
        ]
        service_with_repo.sync_task_alarms(tasks)
        alarms = service_with_repo.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["task_id"] == 1
        assert alarms[0]["task"] == "任务1"

    def test_upsert_task_alarm_creates_new(self, service_with_repo):
        """upsert_task_alarm 应为无提醒的任务创建提醒。"""
        service_with_repo.upsert_task_alarm({"id": 10, "name": "新任鍔", "reminder_time": "2026-05-08 09:00"})
        alarms = service_with_repo.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["task_id"] == 10

    def test_upsert_task_alarm_updates_existing(self, service_with_repo):
        """upsert_task_alarm 应更新已有提醒的 time。"""
        service_with_repo.add_alarm({"time": "旧时闂", "task": "任务", "task_id": 5, "repeat": "不重澶"})
        service_with_repo.upsert_task_alarm({"id": 5, "name": "任务", "reminder_time": "新时闂"})
        alarms = service_with_repo.list_alarms()
        matching = [a for a in alarms if a["task_id"] == 5]
        assert len(matching) == 1
        assert matching[0]["time"] == "新时闂"

    def test_upsert_task_alarm_preserves_triggered_when_time_unchanged(self, service_with_repo):
        """P1-3 回归：upsert 相同时间应保留 triggered 状态。"""
        service_with_repo.add_alarm({"time": "10:00", "task": "任务", "task_id": 5, "triggered": True})
        service_with_repo.upsert_task_alarm({"id": 5, "name": "任务", "reminder_time": "10:00"})
        alarms = service_with_repo.list_alarms()
        matching = [a for a in alarms if a["task_id"] == 5]
        assert len(matching) == 1
        assert matching[0]["triggered"] is True

    def test_upsert_task_alarm_resets_triggered_when_time_changes(self, service_with_repo):
        """P1-3 回归：upsert 不同时间应重置 triggered 状态。"""
        service_with_repo.add_alarm({"time": "10:00", "task": "任务", "task_id": 5, "triggered": True})
        service_with_repo.upsert_task_alarm({"id": 5, "name": "任务", "reminder_time": "11:00"})
        alarms = service_with_repo.list_alarms()
        matching = [a for a in alarms if a["task_id"] == 5]
        assert len(matching) == 1
        assert matching[0]["triggered"] is False
        assert matching[0]["time"] == "11:00"

    def test_upsert_task_alarm_no_reminder_time(self, service_with_repo):
        """鏃"reminder_time 时应无操浣溿€"""
        service_with_repo.upsert_task_alarm({"id": 1, "name": "无提閱"})
        assert len(service_with_repo.list_alarms()) == 0

    def test_upsert_task_alarm_no_id(self, service_with_repo):
        """鏃"id 时应无操浣溿€"""
        service_with_repo.upsert_task_alarm({"name": "无ID", "reminder_time": "10:00"})
        assert len(service_with_repo.list_alarms()) == 0

    def test_sync_task_alarms_preserves_non_task_alarms(self, service_with_repo):
        """sync_task_alarms 应保留不关联任务的提閱掋€"""
        service_with_repo.add_alarm({"time": "08:00", "task": "鐙珛提醒", "repeat": "每天"})
        service_with_repo.sync_task_alarms([])
        alarms = service_with_repo.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["task"] == "鐙珛提醒"

    def test_sync_task_alarms_preserves_triggered_state(self, service_with_repo):
        """P1-3 回归：同步相同提醒时间的任务时应保留 triggered 状态。"""
        service_with_repo.add_alarm({
            "time": "2026-05-07 14:00",
            "task": "任务1",
            "task_id": 1,
            "triggered": True,
            "repeat": "每天",
        })
        service_with_repo.sync_task_alarms([
            {"id": 1, "name": "任务1", "reminder_time": "2026-05-07 14:00"},
        ])
        alarms = service_with_repo.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["triggered"] is True
        assert alarms[0]["repeat"] == "每天"

    def test_sync_task_alarms_resets_triggered_when_time_changes(self, service_with_repo):
        """P1-3 回归：任务提醒时间变化时应重置 triggered 状态。"""
        service_with_repo.add_alarm({
            "time": "2026-05-07 14:00",
            "task": "任务1",
            "task_id": 1,
            "triggered": True,
        })
        service_with_repo.sync_task_alarms([
            {"id": 1, "name": "任务1", "reminder_time": "2026-05-07 15:00"},
        ])
        alarms = service_with_repo.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["triggered"] is False
        assert alarms[0]["time"] == "2026-05-07 15:00"


# ══════════════════════════════════════════════════════════鈺"# 持久鍖"# ══════════════════════════════════════════════════════════鈺"

class TestPersistence:
    def test_alarms_survive_recreate(self, service_with_store, tmp_path):
        """通过 data_store 持久化的提醒在服务重鍚悗仍存鍦ㄣ€"""
        from data.local_storage import LocalDataStore

        service_with_store.add_alarm({"time": "10:00", "task": "持久鍖", "task_id": 1})
        assert len(service_with_store.list_alarms()) == 1

        store2 = LocalDataStore(base_dir=str(tmp_path))
        svc2 = ReminderService(data_store=store2)
        alarms = svc2.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["task_id"] == 1

    def test_alarms_via_repository_survive_recreate(self, mock_repo):
        """通过 reminder_repository 持久化的提醒在重鍚悗仍存鍦ㄣ€"""
        svc1 = ReminderService(reminder_repository=mock_repo)
        svc1.add_alarm({"time": "12:00", "task": "Repo持久鍖", "task_id": 2})

        svc2 = ReminderService(reminder_repository=mock_repo)
        alarms = svc2.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["task_id"] == 2
