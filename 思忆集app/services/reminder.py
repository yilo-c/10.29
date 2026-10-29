"""桌面端提醒服务。"""
from __future__ import annotations

from typing import Any

from repositories import ReminderRepository


class ReminderService:
    # Implements ReminderPort
    def __init__(self, alarms: list[dict[str, Any]] | None = None, data_store: Any = None, reminder_repository: ReminderRepository | None = None) -> None:
        self._reminder_repository = reminder_repository
        self.data_store = data_store
        if alarms is not None:
            self._alarms = list(alarms or [])
            self._persist()
        elif reminder_repository is not None:
            self._alarms = list(reminder_repository.list_alarms())
        else:
            self._alarms = self._load()

    def _repo(self) -> ReminderRepository | None:
        if self._reminder_repository is not None:
            return self._reminder_repository
        return None

    def _serialize_alarm(self, alarm: dict[str, Any]) -> dict[str, Any]:
        item = dict(alarm or {})
        if self.data_store is not None:
            item["time"] = self.data_store.serialize_reminder_time(item.get("time"))
        return item

    def _deserialize_alarm(self, alarm: dict[str, Any]) -> dict[str, Any]:
        item = dict(alarm or {})
        if self.data_store is not None:
            item["time"] = self.data_store.deserialize_reminder_time(item.get("time"))
        return item

    def _load(self) -> list[dict[str, Any]]:
        repo = self._repo()
        if repo is not None:
            return list(repo.list_alarms())
        if self.data_store is None:
            return []
        alarms = self.data_store.load_json("reminders", [])
        return [self._deserialize_alarm(alarm) for alarm in alarms]

    def _persist(self) -> None:
        repo = self._repo()
        if repo is not None:
            repo.replace_alarms(self._alarms)
            return
        if self.data_store is None:
            return
        self.data_store.save_json("reminders", [self._serialize_alarm(alarm) for alarm in self._alarms])

    def list_alarms(self) -> list[dict[str, Any]]:
        return list(self._alarms)

    def replace_alarms(self, alarms: list[dict[str, Any]]) -> None:
        self._alarms = list(alarms or [])
        self._persist()

    def add_alarm(self, alarm_info: dict[str, Any]) -> None:
        self._alarms.append(dict(alarm_info))
        self._persist()

    def sync_task_alarms(self, tasks: list[dict[str, Any]]) -> None:
        # P1-3 修复：保留已有任务提醒的 triggered 状态，避免同步时丢失已触发记录。
        tasks_by_id = {t.get("id"): t for t in tasks if t.get("id") and t.get("reminder_time")}
        existing_by_id = {str(a.get("task_id")): a for a in self._alarms if a.get("task_id")}
        retained_alarms = [alarm for alarm in self._alarms if not alarm.get("task_id")]
        for task_id, task in tasks_by_id.items():
            reminder_time = task.get("reminder_time")
            old_alarm = existing_by_id.get(str(task_id))
            if old_alarm and old_alarm.get("time") == reminder_time:
                retained_alarms.append(old_alarm)
            else:
                retained_alarms.append({
                    "time": reminder_time,
                    "repeat": old_alarm.get("repeat", "不重复") if old_alarm else "不重复",
                    "ringtone": old_alarm.get("ringtone", "默认铃声") if old_alarm else "默认铃声",
                    "triggered": False,
                    "task": task.get("name", ""),
                    "task_id": task_id,
                })
        self._alarms = retained_alarms
        self._persist()

    def upsert_task_alarm(self, task_info: dict[str, Any]) -> None:
        reminder_time = task_info.get("reminder_time")
        task_id = task_info.get("id")
        if not reminder_time or not task_id:
            return None
        alarm_info = {
            "time": reminder_time,
            "repeat": "不重复",
            "ringtone": "默认铃声",
            "triggered": False,
            "task": task_info.get("name", ""),
            "task_id": task_id,
        }
        for index, alarm in enumerate(self._alarms):
            if alarm.get("task_id") == task_id:
                # P1-3 修复：仅当提醒时间变化时才重置 triggered，否则保留原状态。
                if alarm.get("time") == reminder_time:
                    alarm_info["triggered"] = alarm.get("triggered", False)
                    alarm_info["repeat"] = alarm.get("repeat", "不重复")
                    alarm_info["ringtone"] = alarm.get("ringtone", "默认铃声")
                self._alarms[index] = alarm_info
                self._persist()
                return None
        self._alarms.append(alarm_info)
        self._persist()

    def remove_task_alarm(self, task_id: str) -> None:
        if task_id is None:
            return
        self._alarms = [alarm for alarm in self._alarms if alarm.get("task_id") != task_id]
        self._persist()
