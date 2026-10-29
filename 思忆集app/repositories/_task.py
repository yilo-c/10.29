from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.repositories")
from sync.task_sync import build_task_sync_payload, ensure_task_sync_metadata
from sync.note_sync import get_or_create_local_device_id  # P0-2 fix: 之前通过 _protocols * 未导入，导致 NameError


class LocalPersonalTaskRepository:
    # Implements TaskStorePort (via data/_protocols.py)

    """Local personal task repository implementing TaskStorePort."""

    def __init__(self, data_store):

        self.data_store = data_store
        self.content_created_callback = None  # (content_type, id, title) -> None

    def get_task(self, task_id: int | str) -> dict[str, Any] | None:
        """按 ID 直接查找单条任务，跳过 list_tasks 的全量规范化，性能更优。"""
        if self.data_store is None:
            return None
        for task in self.data_store.load_json("tasks", []):
            if task.get("id") == task_id:
                if task.get("deleted_at"):
                    return None
                return task
        return None

    def list_tasks(self, include_deleted: bool = False) -> list[dict[str, Any]]:

        if self.data_store is None:
            return []

        tasks = self.data_store.load_json("tasks", [])

        changed = False

        max_id = max((task.get("id", 0) or 0 for task in tasks), default=0)

        for task in tasks:
            if not task.get("id"):
                max_id += 1

                task["id"] = max_id

                changed = True

            is_completed = bool(task.get("is_completed", task.get("completed", False)))

            if task.get("is_completed") != is_completed or task.get("completed") != is_completed:
                task["is_completed"] = is_completed

                task["completed"] = is_completed

                changed = True

            serialized_time = self.data_store.serialize_reminder_time(task.get("reminder_time"))

            if task.get("reminder_time") != serialized_time:
                task["reminder_time"] = serialized_time

                changed = True

            if not task.get("created_at"):
                task["created_at"] = datetime.now(timezone.utc).isoformat()

                changed = True

            if not task.get("updated_at"):
                task["updated_at"] = task.get("created_at")

                changed = True

            if ensure_task_sync_metadata(task, self.data_store):
                changed = True

        if changed:
            self.data_store.save_json("tasks", tasks)

        if include_deleted:
            return tasks

        return [task for task in tasks if not task.get("deleted_at")]

    def add_task(self, task_info: dict[str, Any]) -> int | str:

        if self.data_store is None:
            return 0

        tasks = self.data_store.load_json("tasks", [])

        desired_id = task_info.get("id")
        if desired_id and all(task.get("id") != desired_id for task in tasks):
            new_id = desired_id
        else:
            new_id = self.data_store.next_id(tasks)

        task = dict(task_info)

        # 标题字段兼容：存储层统一用 title，兼容 text（手机端）/name（既有数据）
        if not task.get("title") and (task.get("text") or task.get("name")):
            task["title"] = task.get("text") or task.get("name")
        # AM-2 闭环：canonical name 存在而 title 缺失时同步回填，
        # 保证 update_task 的 title↔name 归一往返一致
        if not task.get("name") and task.get("title"):
            task["name"] = task["title"]

        task["id"] = new_id

        task["is_completed"] = bool(task.get("is_completed", task.get("completed", False)))

        task["completed"] = task["is_completed"]

        task["reminder_time"] = self.data_store.serialize_reminder_time(task.get("reminder_time"))

        now = datetime.now(timezone.utc).isoformat()

        task.setdefault("created_at", now)

        task.setdefault("updated_at", now)

        task.setdefault("deleted_at", None)

        ensure_task_sync_metadata(task, self.data_store, mark_dirty=True)

        tasks.append(task)

        self.data_store.save_json("tasks", tasks)

        if self.content_created_callback is not None:
            title = task_info.get("title", "") or task_info.get("text", "")
            self.content_created_callback("task", new_id, title)

        return new_id

    def update_task(self, task_id: int | str, new_info: dict[str, Any]) -> bool:
        """更新任务。S1-2（对齐 S0-1 note 面 bool 契约）：True=真实写入，
        False=id 未命中 / 回收站软删拒编辑（AN-2）。"""

        if self.data_store is None:
            return False

        tasks = self.data_store.load_json("tasks", [])

        for task in tasks:
            if task.get("id") == task_id:
                # AN-2：回收站条目拒绝普通编辑；显式携带 deleted_at 的快照写入
                # （audit_rollback 恢复 / 同步合并路径）放行。
                if task.get("deleted_at") and "deleted_at" not in new_info:
                    logger.warning("update_task: task %s 在回收站中，拒绝编辑", task_id)
                    return False
                # AM-2：title↔name 双向归一（存储 canonical=name，handler 契约=title）
                if new_info.get("title") is not None and new_info.get("name") is None:
                    new_info = dict(new_info)
                    new_info["name"] = new_info["title"]
                elif new_info.get("name") is not None and new_info.get("title") is None:
                    new_info = dict(new_info)
                    new_info["title"] = new_info["name"]
                task.update(new_info)

                task["is_completed"] = bool(task.get("is_completed", task.get("completed", False)))

                task["completed"] = task["is_completed"]

                task["reminder_time"] = self.data_store.serialize_reminder_time(task.get("reminder_time"))

                if "version" not in new_info:
                    task["version"] = max(int(task.get("version") or 0), 1) + 1

                # V2.1 批次3（P1-3）：仅当 new_info 未提供 updated_at 时回填 now——
                # 同步合并（sync_state 来自远端）必须保留远端时间戳，避免本地 now 覆盖。
                if "updated_at" not in new_info:
                    task["updated_at"] = datetime.now(timezone.utc).isoformat()

                task.setdefault("deleted_at", None)

                ensure_task_sync_metadata(task, self.data_store, mark_dirty="sync_state" not in new_info)

                self.data_store.save_json("tasks", tasks)
                return True

        self.data_store.save_json("tasks", tasks)
        return False

    def toggle_task_completion(self, task_id: int | str, is_completed: bool) -> None:

        if self.data_store is None:
            return

        tasks = self.data_store.load_json("tasks", [])

        for task in tasks:
            if task.get("id") == task_id:
                task["is_completed"] = bool(is_completed)

                task["completed"] = task["is_completed"]

                task["version"] = max(int(task.get("version") or 0), 1) + 1

                task["updated_at"] = datetime.now(timezone.utc).isoformat()

                ensure_task_sync_metadata(task, self.data_store, mark_dirty=True)

                break

        self.data_store.save_json("tasks", tasks)

    def delete_task(self, task_id: int | str, soft: bool = True) -> None:

        if self.data_store is None:
            return

        tasks = self.data_store.load_json("tasks", [])

        if soft:
            for task in tasks:
                if task.get("id") == task_id:
                    task["deleted_at"] = datetime.now(timezone.utc).isoformat()

                    task["version"] = max(int(task.get("version") or 0), 1) + 1

                    task["updated_at"] = task["deleted_at"]

                    ensure_task_sync_metadata(task, self.data_store, mark_dirty=True)

                    break

        else:
            tasks = [task for task in tasks if task.get("id") != task_id]

        self.data_store.save_json("tasks", tasks)

    def permanent_delete_task(self, task_id: int | str) -> bool:
        """物理摘除回收站中的软删任务（S1-2/AN-2，对齐 notes 语义）。

        活任务（未软删）拒绝返回 False。kv 侧无 id_map，摘除即从 tasks 列表剔除。
        """
        if self.data_store is None:
            return False
        tasks = self.data_store.load_json("tasks", [])
        target = next((t for t in tasks if t.get("id") == task_id), None)
        if target is None or not target.get("deleted_at"):
            return False
        tasks = [t for t in tasks if t.get("id") != task_id]
        self.data_store.save_json("tasks", tasks)
        return True

    def build_task_sync_payload(self, task_id: int | str) -> dict[str, Any] | None:

        for task in self.list_tasks(include_deleted=True):
            if task.get("id") == task_id:
                return build_task_sync_payload(task)

        return None

    def list_pending_sync_tasks(self) -> list[dict[str, Any]]:

        pending_tasks = []

        for task in self.list_tasks(include_deleted=True):
            if task.get("sync_state") in {"local_only", "dirty"}:
                pending_tasks.append(dict(task))

        return pending_tasks

    def mark_task_as_synced(self, task_id: int | str, remote_task: dict[str, Any]) -> None:

        if self.data_store is None:
            return

        def _apply(tasks: Any) -> list[dict[str, Any]]:

            if not isinstance(tasks, list):
                return []

            for task in tasks:
                if task.get("id") != task_id:
                    continue

                synced = remote_task or {}

                remote_id = synced.get("remote_id") or synced.get("id")

                if isinstance(remote_id, str) and remote_id.strip():
                    task["remote_id"] = remote_id.strip()

                task["sync_state"] = "synced"

                task["last_synced_at"] = synced.get("last_synced_at") or synced.get("updated_at") or datetime.now(timezone.utc).isoformat()

                if synced.get("version") is not None:
                    task["version"] = max(int(synced.get("version") or 1), 1)

                if synced.get("created_at"):
                    task["created_at"] = synced.get("created_at")

                if synced.get("updated_at"):
                    task["updated_at"] = synced.get("updated_at")

                if "deleted_at" in synced:
                    task["deleted_at"] = synced.get("deleted_at")

                break

            return tasks

        self.data_store.update_json("tasks", [], _apply)

    def resolve_local_only_deleted_task(self, task_id: int | str) -> None:

        if self.data_store is None:
            return

        self.data_store.update_json(
            "tasks",
            [],
            lambda tasks: [task for task in tasks if task.get("id") != task_id] if isinstance(tasks, list) else [],
        )

    def merge_remote_tasks(self, remote_tasks: list[dict[str, Any]], prefer_remote: bool = False) -> dict[str, Any]:

        # 原子性钩子：PRE-SYNC 蹇?収

        local_state_before = {t.get("id"): (t.get("sync_state"), t.get("version", 0)) for t in self.list_tasks(include_deleted=True)}

        tasks = self.list_tasks(include_deleted=True)

        remote_index = {str(task.get("remote_id")): task for task in tasks if isinstance(task.get("remote_id"), str) and task.get("remote_id")}

        changed = False

        added = 0

        updated = 0

        skipped = 0

        conflicts: list[dict[str, Any]] = []

        next_id = self.data_store.next_id(tasks) if self.data_store is not None else 1

        for remote_task in remote_tasks or []:
            if not isinstance(remote_task, dict):
                continue

            remote_key = self._resolve_remote_task_key(remote_task)

            if not remote_key:
                continue

            existing_task = remote_index.get(remote_key)

            if existing_task is None:
                tasks.append(self._build_local_task_from_remote(remote_task, local_id=next_id))

                remote_index[remote_key] = tasks[-1]

                next_id += 1

                added += 1

                changed = True

                continue

            if not self._is_remote_task_different(existing_task, remote_task):
                if existing_task.get("sync_state") != "synced":
                    existing_task["sync_state"] = "synced"

                    existing_task["last_synced_at"] = remote_task.get("last_synced_at") or remote_task.get("updated_at")

                    updated += 1

                    changed = True

                continue

            if existing_task.get("sync_state") in {"local_only", "dirty"} and not prefer_remote:
                skipped += 1

                conflicts.append(
                    {
                        "local_id": existing_task.get("id"),
                        "remote_id": remote_key,
                        "title": existing_task.get("name") or remote_task.get("name") or "鏈?懡名任鍔?",
                        "local_title": existing_task.get("name") or "鏈?懡名任鍔?",
                        "remote_title": remote_task.get("name") or "鏈?懡名任鍔?",
                        "local_updated_at": existing_task.get("updated_at") or existing_task.get("created_at"),
                        "remote_updated_at": remote_task.get("updated_at") or remote_task.get("created_at"),
                        "local_completed": bool(existing_task.get("is_completed", existing_task.get("completed", False))),
                        "remote_completed": bool(remote_task.get("is_completed", remote_task.get("completed", False))),
                        "local_reminder_time": existing_task.get("reminder_time"),
                        "remote_reminder_time": remote_task.get("reminder_time"),
                    }
                )

                continue

            merged_task = self._build_local_task_from_remote(
                remote_task,
                local_id=existing_task.get("id"),
                existing_task=existing_task,
            )

            existing_index = tasks.index(existing_task)

            tasks[existing_index] = merged_task

            remote_index[remote_key] = merged_task

            updated += 1

            changed = True

        if changed and self.data_store is not None:
            self.data_store.save_json("tasks", tasks)

        # 原子性钩子：POST-SYNC 验证

        local_state_after = {t.get("id"): (t.get("sync_state"), t.get("version", 0)) for t in tasks}

        for task_id in local_state_before:
            if task_id in local_state_after:
                state_before, _ = local_state_before[task_id]

                state_after, _ = local_state_after[task_id]

                if state_before in {"local_only", "dirty"} and state_after not in {"synced", "deleted"}:
                    logging.getLogger(__name__).warning(
                        "sync_atomicity_check: task %s incomplete state transition %s鈫?s", task_id, state_before, state_after
                    )

        return {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "conflicts": conflicts,
            "total_remote": len([item for item in (remote_tasks or []) if isinstance(item, dict)]),
        }

    def get_remote_snapshot_tasks(self) -> list[dict[str, Any]]:

        if self.data_store is None:
            return []

        data = self.data_store.load_json("remote_snapshot_tasks", [])

        return data or []

    def set_remote_snapshot_tasks(self, tasks: list[dict[str, Any]]) -> None:

        if self.data_store is None:
            return

        self.data_store.save_json("remote_snapshot_tasks", tasks)

    def get_cloud_tasks(self) -> list[dict[str, Any]]:

        return self.get_remote_snapshot_tasks()

    def set_cloud_tasks(self, tasks: list[dict[str, Any]]) -> None:

        self.set_remote_snapshot_tasks(tasks)

    def find_remote_snapshot_task_by_remote_id(self, remote_id: str) -> dict[str, Any] | None:

        remote_key = str(remote_id or "").strip()

        if not remote_key:
            return None

        for task in self.get_remote_snapshot_tasks():
            if not isinstance(task, dict):
                continue

            if self._resolve_remote_task_key(task) == remote_key:
                return dict(task)

        return None

    def find_cloud_task_by_remote_id(self, remote_id: str) -> dict[str, Any] | None:

        return self.find_remote_snapshot_task_by_remote_id(remote_id)

    def overwrite_task_with_remote(self, task_id: int | str, remote_task: dict[str, Any]) -> bool:

        if self.data_store is None:
            return False

        tasks = self.data_store.load_json("tasks", [])

        for index, task in enumerate(tasks):
            if task.get("id") != task_id:
                continue

            tasks[index] = self._build_local_task_from_remote(
                remote_task,
                local_id=task_id,
                existing_task=task,
            )

            self.data_store.save_json("tasks", tasks)

            return True

        return False

    def _resolve_remote_task_key(self, remote_task: dict[str, Any]) -> str | None:

        for candidate in (remote_task.get("remote_id"), remote_task.get("id")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        return None

    def _build_local_task_from_remote(
        self,
        remote_task: dict[str, Any],
        *,
        local_id: int | str,
        existing_task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        now_iso = datetime.now(timezone.utc).isoformat()

        remote_key = self._resolve_remote_task_key(remote_task)

        merged_task = dict(existing_task or {})

        merged_task.update(
            {
                "id": local_id,
                "remote_id": remote_key,
                "name": remote_task.get("name") or "鏈?懡名任鍔?",
                "note": remote_task.get("note") or "",
                "is_completed": bool(remote_task.get("is_completed", remote_task.get("completed", False))),
                "completed": bool(remote_task.get("is_completed", remote_task.get("completed", False))),
                "reminder_time": self.data_store.serialize_reminder_time(remote_task.get("reminder_time"))
                if self.data_store is not None
                else remote_task.get("reminder_time"),
                "linked_note_id": remote_task.get("linked_note_id"),
                "created_at": remote_task.get("created_at") or merged_task.get("created_at") or now_iso,
                "updated_at": remote_task.get("updated_at") or merged_task.get("updated_at") or now_iso,
                "deleted_at": remote_task.get("deleted_at"),
                "version": max(int(remote_task.get("version") or 1), 1),
                "device_id": remote_task.get("device_id") or merged_task.get("device_id") or get_or_create_local_device_id(self.data_store),
                "sync_state": "synced",
                "last_synced_at": remote_task.get("last_synced_at") or remote_task.get("updated_at") or now_iso,
            }
        )

        ensure_task_sync_metadata(merged_task, self.data_store)

        merged_task["sync_state"] = "synced"

        return merged_task

    def _is_remote_task_different(self, local_task: dict[str, Any], remote_task: dict[str, Any]) -> bool:

        comparable_fields = (
            "name",
            "note",
            "reminder_time",
            "linked_note_id",
            "updated_at",
            "deleted_at",
            "version",
        )

        for field_name in comparable_fields:
            if local_task.get(field_name) != remote_task.get(field_name):
                return True

        if bool(local_task.get("is_completed", local_task.get("completed", False))) != bool(
            remote_task.get("is_completed", remote_task.get("completed", False))
        ):
            return True

        return False

    def search_tasks(self, query: str, max_results: int = 50) -> list[dict[str, Any]]:
        """对本地任务做多字段全文搜索，返回按相关度排序的结果列表。"""
        from data.local_storage import LocalDataStore

        all_tasks = self.list_tasks(include_deleted=False)

        return LocalDataStore.fts_search_items(all_tasks, query, ["name", "note", "tags"], max_results=max_results)
