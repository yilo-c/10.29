"""Cloud Sync Entity Handlers — 实体采集与合并 mixin。

从 CloudSyncClient 拆出：notes/todos/settings 三类实体的本地采集
(_collect_*) 与远端合并 (_merge_*)。方法通过 self 访问仓库与状态，
由 CloudSyncClient 继承混入，外部调用不变。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sync._cloud_sync_crypto import (
    _normalize_ts,
    _detect_conflict,
    RESOURCE_NOTES,
    RESOURCE_TODOS,
    RESOURCE_SETTINGS,
    SYNCABLE_SETTING_KEYS,
    logger,
)


class SyncEntityHandlersMixin:
    """实体采集/合并。通过继承混入 CloudSyncClient。"""

    # 宿主契约声明：由 CloudSyncClient 在 __init__ 注入（见 sync/cloud_sync_client.py）
    if TYPE_CHECKING:
        note_repo: Any
        task_repo: Any
        settings_repo: Any
        _emit_sync_event: Any

    def _collect_notes(self) -> list[dict[str, Any]]:
        if not self.note_repo:
            return []
        try:
            notes = self.note_repo.list_notes()
        except Exception as exc:
            logger.warning("list_notes failed: %s", exc)
            return []
        result: list[dict[str, Any]] = []
        for n in notes:
            # M24: 推送软删除标记（而非跳过），让远端与其他设备同步删除，
            # 避免已删数据在 pull 时复活。
            deleted = bool(n.get("deleted_at"))
            result.append(
                {
                    "id": n.get("id"),
                    "title": n.get("title", ""),
                    "content": n.get("content", ""),
                    "type": n.get("type", "note"),
                    "folderId": n.get("folder") or n.get("folderId"),
                    "tags": n.get("tags", []),
                    "createdAt": n.get("created_at") or n.get("createdAt") or 0,
                    "updatedAt": n.get("updated_at") or n.get("updatedAt") or 0,
                    "deleted": deleted,
                }
            )
        return result

    def _collect_todos(self) -> list[dict[str, Any]]:
        if not self.task_repo:
            return []
        try:
            tasks = self.task_repo.list_tasks()
        except Exception as exc:
            logger.warning("list_tasks failed: %s", exc)
            return []
        result: list[dict[str, Any]] = []
        for t in tasks:
            # M24: 推送软删除标记（而非跳过），让远端与其他设备同步删除。
            deleted = bool(t.get("deleted_at"))
            created = t.get("created_at") or t.get("createdAt") or 0
            updated = t.get("updated_at") or t.get("updatedAt") or created
            result.append(
                {
                    "id": t.get("id"),
                    "text": t.get("title") or t.get("text", ""),
                    "done": bool(t.get("completed") or t.get("done", False)),
                    "category": t.get("category", ""),
                    "priority": t.get("priority", "normal"),
                    "dueDate": t.get("due_date") or t.get("dueDate"),
                    "createdAt": created,
                    "updatedAt": updated,
                    "deleted": deleted,
                }
            )
        return result

    def _collect_settings(self) -> dict[str, Any]:
        if not self.settings_repo:
            return {}
        result: dict[str, Any] = {}
        for key in SYNCABLE_SETTING_KEYS:
            val = self.settings_repo.get_setting(key, None)
            if val is not None:
                result[key] = val
        return result

    def _collect_resource(self, resource: str) -> Any:
        if resource == RESOURCE_NOTES:
            return self._collect_notes()
        if resource == RESOURCE_TODOS:
            return self._collect_todos()
        if resource == RESOURCE_SETTINGS:
            return self._collect_settings()
        return None

    def _merge_notes(self, remote_notes: list[dict[str, Any]]) -> int:
        """LWW + 冲突检测合并 notes 到本地。返回合并的条目数。

        P1-8 修复：
        1. 时间戳归一化 —— 桌面端用 ISO 字符串、手机端用毫秒 epoch、
           Worker 用秒 epoch，旧代码直接比较会 TypeError 或得到错误结果。
        2. 冲突检测 —— 若本地和远端自上次同步后都被修改过（真冲突），
           保留本地版本到 ``versions`` 历史并标记 ``sync_state=conflict``，
           避免静默丢数据。非冲突时仍走 LWW。
        """
        if not self.note_repo:
            return 0
        try:
            local_notes = {n.get("id"): n for n in self.note_repo.list_notes()}
        except Exception as exc:
            logger.warning("merge notes: list failed: %s", exc)
            return 0

        merged = 0
        conflicts = 0
        deletions = 0
        for remote in remote_notes:
            rid = remote.get("id")
            if not rid:
                continue
            local = local_notes.get(rid)
            remote_time = _normalize_ts(remote.get("updatedAt") or remote.get("createdAt"))
            local_time = _normalize_ts(local.get("updated_at") or local.get("updatedAt")) if local else 0.0
            # M24: 远端已删除 → 把删除传播到本地（软删除）。
            # V2.1 批次3（P1-2）：本地若有未同步修改（dirty），远端删除不得无视 dirty
            # 直接删——标 sync_state=conflict 保留本地版本，交用户裁决（不静默丢数据）。
            if remote.get("deleted"):
                if local and not local.get("deleted_at"):
                    if _detect_conflict(local, remote_time):
                        conflicts += 1
                        logger.warning(
                            "sync delete-vs-dirty conflict on note %s: local has unsynced "
                            "edits, remote deleted — preserving local (sync_state=conflict)",
                            rid,
                        )
                        self._emit_sync_event(
                            "sync.conflict",
                            {
                                "resource": "note",
                                "id": rid,
                                "title": str(local.get("title") or "")[:80],
                                "local_time": local_time,
                                "remote_time": remote_time,
                                "kind": "remote_delete_vs_local_dirty",
                            },
                        )
                        try:
                            self.note_repo.update_note(
                                rid,
                                {
                                    "sync_state": "conflict",
                                    "last_synced_at": remote.get("updatedAt"),
                                },
                            )
                        except Exception as exc:
                            logger.warning("mark delete-conflict note %s failed: %s", rid, exc)
                        continue
                    try:
                        self.note_repo.delete_note(rid, soft=True)
                        deletions += 1
                    except Exception as exc:
                        logger.warning("propagate delete note %s failed: %s", rid, exc)
                continue
            # P1-8: 冲突检测 —— 双方都有未同步修改（local 缺失时无冲突，纯 LWW）
            if local and _detect_conflict(local, remote_time):
                conflicts += 1
                logger.warning(
                    "sync conflict on note %s: local_time=%s remote_time=%s last_synced=%s — preserving local as versioned copy",
                    rid,
                    local_time,
                    remote_time,
                    _normalize_ts(local.get("last_synced_at")),
                )
                # P0-2: 冲突可见 —— 发 sync.conflict 事件供前端/手机端提示裁决
                self._emit_sync_event(
                    "sync.conflict",
                    {
                        "resource": "note",
                        "id": rid,
                        "title": str(remote.get("title") or local.get("title") or "")[:80],
                        "local_time": local_time,
                        "remote_time": remote_time,
                    },
                )
                # 保留本地版本到 versions 历史（update_note 已自动保存旧版本）
                # 但用远端覆盖当前内容，并标记冲突状态供 UI 提示
                # M23: 持久化 last_synced_at 供下次冲突检测使用
                note_data = {
                    "id": rid,
                    "title": remote.get("title", ""),
                    "content": remote.get("content", ""),
                    "type": remote.get("type", "note"),
                    "folder": remote.get("folderId"),
                    "tags": remote.get("tags", []),
                    "created_at": remote.get("createdAt"),
                    "updated_at": remote.get("updatedAt"),
                    "last_synced_at": remote.get("updatedAt"),
                    "sync_state": "conflict",
                }
                try:
                    self.note_repo.update_note(rid, note_data)
                    merged += 1
                except Exception as exc:
                    logger.warning("merge conflict note %s failed: %s", rid, exc)
                continue

            if local and remote_time <= local_time:
                continue
            # 本地不存在或远端更新 → upsert
            # M23: 持久化 last_synced_at 供下次冲突检测使用
            note_data = {
                "id": rid,
                "title": remote.get("title", ""),
                "content": remote.get("content", ""),
                "type": remote.get("type", "note"),
                "folder": remote.get("folderId"),
                "tags": remote.get("tags", []),
                "created_at": remote.get("createdAt"),
                "updated_at": remote.get("updatedAt"),
                "last_synced_at": remote.get("updatedAt"),
            }
            try:
                if local:
                    self.note_repo.update_note(rid, note_data)
                else:
                    self.note_repo.add_note(note_data)
                merged += 1
            except Exception as exc:
                logger.warning("merge note %s failed: %s", rid, exc)
        if deletions:
            logger.info("merge notes: propagated %d deletion(s) from remote", deletions)
        if conflicts:
            logger.warning("merge notes: %d conflict(s) detected and preserved", conflicts)
        return merged

    def _merge_todos(self, remote_todos: list[dict[str, Any]]) -> int:
        """LWW + 冲突检测合并 todos 到本地 task_repo。返回合并的条目数。

        P1-8 修复：与 _merge_notes 同样的时间戳归一化 + 冲突检测。
        """
        if not self.task_repo:
            return 0
        try:
            local_tasks = {t.get("id"): t for t in self.task_repo.list_tasks()}
        except Exception as exc:
            logger.warning("merge todos: list failed: %s", exc)
            return 0

        merged = 0
        conflicts = 0
        deletions = 0
        for remote in remote_todos:
            rid = remote.get("id")
            if not rid:
                continue
            local = local_tasks.get(rid)
            remote_time = _normalize_ts(remote.get("updatedAt") or remote.get("createdAt"))
            local_time = _normalize_ts(local.get("updated_at") or local.get("updatedAt")) if local else 0.0
            # M24: 远端已删除 → 把删除传播到本地（软删除）。
            # V2.1 批次3（P1-2）：本地有未同步修改时，远端删除不得无视 dirty 直接删。
            if remote.get("deleted"):
                if local and not local.get("deleted_at"):
                    if _detect_conflict(local, remote_time):
                        conflicts += 1
                        logger.warning(
                            "sync delete-vs-dirty conflict on todo %s: local has unsynced "
                            "edits, remote deleted — preserving local (sync_state=conflict)",
                            rid,
                        )
                        self._emit_sync_event(
                            "sync.conflict",
                            {
                                "resource": "todo",
                                "id": rid,
                                "title": str(local.get("title") or local.get("text") or "")[:80],
                                "local_time": local_time,
                                "remote_time": remote_time,
                                "kind": "remote_delete_vs_local_dirty",
                            },
                        )
                        try:
                            self.task_repo.update_task(
                                rid,
                                {
                                    "sync_state": "conflict",
                                    "last_synced_at": remote.get("updatedAt"),
                                },
                            )
                        except Exception as exc:
                            logger.warning("mark delete-conflict todo %s failed: %s", rid, exc)
                        continue
                    try:
                        self.task_repo.delete_task(rid, soft=True)
                        deletions += 1
                    except Exception as exc:
                        logger.warning("propagate delete todo %s failed: %s", rid, exc)
                continue
            # P1-8: 冲突检测（local 缺失时无冲突，纯 LWW）
            if local and _detect_conflict(local, remote_time):
                conflicts += 1
                logger.warning(
                    "sync conflict on todo %s: local_time=%s remote_time=%s last_synced=%s — preserving local as versioned copy",
                    rid,
                    local_time,
                    remote_time,
                    _normalize_ts(local.get("last_synced_at")),
                )
                # P0-2: 冲突可见 —— 发 sync.conflict 事件供前端/手机端提示裁决
                self._emit_sync_event(
                    "sync.conflict",
                    {
                        "resource": "todo",
                        "id": rid,
                        "title": str(remote.get("text") or local.get("title") or "")[:80],
                        "local_time": local_time,
                        "remote_time": remote_time,
                    },
                )
                # P0-2: task repo 无自动版本历史，冲突覆盖前手动快照本地版，
                # 供 sync.resolve_conflict choice=local 恢复（防静默丢本地修改）
                local_snapshot = {k: v for k, v in dict(local).items() if k != "versions"}
                prev_versions = list(local.get("versions") or [])[:4]
                # M23: 持久化 last_synced_at 供下次冲突检测使用
                task_data = {
                    "id": rid,
                    "title": remote.get("text", ""),
                    "completed": bool(remote.get("done", False)),
                    "category": remote.get("category", ""),
                    "priority": remote.get("priority", "normal"),
                    "due_date": remote.get("dueDate"),
                    "created_at": remote.get("createdAt"),
                    "updated_at": remote.get("updatedAt"),
                    "last_synced_at": remote.get("updatedAt"),
                    "sync_state": "conflict",
                    "versions": [local_snapshot, *prev_versions],
                }
                try:
                    self.task_repo.update_task(rid, task_data)
                    merged += 1
                except Exception as exc:
                    logger.warning("merge conflict todo %s failed: %s", rid, exc)
                continue

            if local and remote_time <= local_time:
                continue
            # M23: 持久化 last_synced_at 供下次冲突检测使用
            task_data = {
                "id": rid,
                "title": remote.get("text", ""),
                "completed": bool(remote.get("done", False)),
                "category": remote.get("category", ""),
                "priority": remote.get("priority", "normal"),
                "due_date": remote.get("dueDate"),
                "created_at": remote.get("createdAt"),
                "updated_at": remote.get("updatedAt"),
                "last_synced_at": remote.get("updatedAt"),
            }
            try:
                if local:
                    self.task_repo.update_task(rid, task_data)
                else:
                    self.task_repo.add_task(task_data)
                merged += 1
            except Exception as exc:
                logger.warning("merge todo %s failed: %s", rid, exc)
        if deletions:
            logger.info("merge todos: propagated %d deletion(s) from remote", deletions)
        if conflicts:
            logger.warning("merge todos: %d conflict(s) detected and preserved", conflicts)
        return merged

    def _merge_settings(self, remote_settings: dict[str, Any]) -> int:
        """合并 settings（仅白名单字段）。返回合并的字段数。"""
        if not self.settings_repo or not isinstance(remote_settings, dict):
            return 0
        merged = 0
        for key in SYNCABLE_SETTING_KEYS:
            if key in remote_settings:
                try:
                    self.settings_repo.set_setting(key, remote_settings[key])
                    merged += 1
                except Exception as exc:
                    logger.warning("merge setting %s failed: %s", key, exc)
        return merged
