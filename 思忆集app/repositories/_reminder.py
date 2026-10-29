from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ._protocols import *

logger = logging.getLogger("siyi.repositories")
class LocalReminderRepository:

    """本地提醒数据仓储，直接读写 reminders.json。"""



    def __init__(self, data_store):

        self.data_store = data_store



    def _serialize_alarm(self, alarm):

        item = dict(alarm or {})

        if self.data_store is not None:

            item["time"] = self.data_store.serialize_reminder_time(item.get("time"))

        return item



    def _deserialize_alarm(self, alarm):

        item = dict(alarm or {})

        if self.data_store is not None:

            item["time"] = self.data_store.deserialize_reminder_time(item.get("time"))

        return item



    def _load(self):

        if self.data_store is None:

            return []

        alarms = self.data_store.load_json("reminders", [])

        return [self._deserialize_alarm(alarm) for alarm in alarms]



    def _persist(self, alarms):

        if self.data_store is None:

            return

        self.data_store.save_json("reminders", [self._serialize_alarm(alarm) for alarm in alarms])



    def list_alarms(self) -> list[dict[str, Any]]:

        return self._load()



    def add_alarm(self, alarm_info: dict[str, Any]) -> None:

        if self.data_store is None:

            return

        def _apply(alarms: Any) -> list[dict[str, Any]]:

            if not isinstance(alarms, list):

                alarms = []

            alarms.append(self._serialize_alarm(dict(alarm_info)))

            return alarms



        self.data_store.update_json("reminders", [], _apply)



    def replace_alarms(self, alarms: list[dict[str, Any]]) -> None:

        self._persist(list(alarms or []))



    def upsert_task_alarm(self, task_info: dict[str, Any]) -> None:

        reminder_time = task_info.get("reminder_time")

        task_id = task_info.get("id")

        if not reminder_time or not task_id:

            return

        alarm_info = {

            "time": reminder_time,

            "repeat": "不重复",

            "ringtone": "默认铃声",

            "triggered": False,

            "task": task_info.get("name", ""),

            "task_id": task_id,

        }

        alarms = self._load()

        for index, alarm in enumerate(alarms):

            if alarm.get("task_id") == task_id:

                # P1-3 修复：仅当提醒时间变化时才重置 triggered，否则保留原状态。

                if alarm.get("time") == reminder_time:

                    alarm_info["triggered"] = alarm.get("triggered", False)

                    alarm_info["repeat"] = alarm.get("repeat", "不重复")

                    alarm_info["ringtone"] = alarm.get("ringtone", "默认铃声")

                alarms[index] = alarm_info

                self._persist(alarms)

                return

        alarms.append(alarm_info)

        self._persist(alarms)



    def remove_task_alarm(self, task_id: int) -> None:

        if task_id is None or self.data_store is None:

            return

        def _apply(alarms: Any) -> list[dict[str, Any]]:

            if not isinstance(alarms, list):

                return []

            return [alarm for alarm in alarms if alarm.get("task_id") != task_id]



        self.data_store.update_json("reminders", [], _apply)



    def sync_task_alarms(self, tasks: list[dict[str, Any]]) -> None:

        # P1-3 修复：保留已有任务提醒的 triggered 状态，避免同步时丢失已触发记录。

        alarms = self._load()

        tasks_by_id = {t.get("id"): t for t in tasks if t.get("id") and t.get("reminder_time")}

        existing_by_id = {str(a.get("task_id")): a for a in alarms if a.get("task_id")}

        retained_alarms = [alarm for alarm in alarms if not alarm.get("task_id")]

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

        self._persist(retained_alarms)

