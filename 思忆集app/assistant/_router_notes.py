"""Notes/journal capability mixin — extracted from capability_router.py."""

from __future__ import annotations

import logging
import re as _re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from core.settings_schema import KEY_ASSISTANT_ALLOW_SELF_UPDATE

logger = logging.getLogger("siyi.assistant.autonomy.router.notes")


class NotesRouterHost(Protocol):
    """NotesRouterMixin 依赖的宿主契约 — AssistantCapabilityRouter 由 __init__ 注入。"""

    task_repository: Any
    note_repository: Any
    write_guard: Any
    inbox_repository: Any
    sync_orchestrator: Any
    data_store: Any
    environment_probe: Any
    settings_repository: Any


class NotesRouterMixin:
    """Notes and journal capability handlers."""

    if TYPE_CHECKING:
        task_repository: Any
        note_repository: Any
        write_guard: Any
        inbox_repository: Any
        sync_orchestrator: Any
        data_store: Any
        environment_probe: Any
        settings_repository: Any

    def _journal_daily_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = str(payload.get("title") or "每日日志草稿").strip()
        tasks = self.task_repository.list_tasks(include_deleted=False)
        pending = [task for task in tasks if not bool(task.get("is_completed", task.get("completed", False)))]
        notes = self.note_repository.list_notes(include_deleted=False)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        content_lines = [
            f"# {title}",
            "",
            f"生成时间: {now}",
            "",
            "## 今日进展",
            "- 自动汇总待补充",
            "",
            "## 待办摘要",
            f"- 未完成任务: {len(pending)}",
            "",
            "## 笔记摘要",
            f"- 当前笔记总量: {len(notes)}",
            "",
            "## 明日计划",
            "- 补充关键目标与优先级",
        ]
        new_note_data = {
            "title": title,
            "content": "\n".join(content_lines),
            "folder_path": "/助手日志",
            "tags": ["assistant", "daily"],
            "category": "assistant",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "favorite": False,
        }
        if self.write_guard is not None:
            with self.write_guard.protect("note", "", actor="assistant", action_id="journal.daily_draft", operation="create", before_snapshot=None) as guard:
                note_id = self.note_repository.add_note(new_note_data)
                guard.set_after({"note_id": note_id, **new_note_data})
        else:
            note_id = self.note_repository.add_note(new_note_data)
        try:
            inbox_data = {
                "title": f"日志已生成: {title}",
                "content": f"note_id={note_id}",
                "tags": ["assistant", "journal"],
                "status": "new",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if self.write_guard is not None:
                with self.write_guard.protect("inbox", "", actor="assistant", action_id="journal.daily_draft", operation="create", before_snapshot=None) as guard:
                    self.inbox_repository.add_entry(inbox_data)
                    guard.set_after(inbox_data)
            else:
                self.inbox_repository.add_entry(inbox_data)
        except Exception:
            logger.warning("journal inbox entry creation failed", exc_info=True)
        return {"note_id": note_id, "pending_tasks": len(pending), "note_count": len(notes)}

    def _notes_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.sync_orchestrator is None:
            return {"synced": False, "reason": "sync_orchestrator_unavailable"}
        try:
            before_snapshot = None
            if self.write_guard is not None:
                try:
                    notes = self.note_repository.list_notes(include_deleted=False)
                    before_snapshot = {"note_count": len(notes), "snapshot_at": datetime.now(timezone.utc).isoformat()}
                except Exception:
                    logger.warning("同步前快照笔记列表失败")
            if self.write_guard is not None:
                with self.write_guard.protect("json_store", "notes", actor="assistant", action_id="notes.sync", operation="sync", before_snapshot=before_snapshot) as guard:
                    result = self.sync_orchestrator.sync_resource("notes", limit=100)
                    guard.set_after({"result": result, "synced_at": datetime.now(timezone.utc).isoformat()})
            else:
                result = self.sync_orchestrator.sync_resource("notes", limit=100)
            return {"synced": True, "result": result, "mode": payload.get("mode") or "bidirectional"}
        except Exception as exc:
            return {"synced": False, "reason": "sync_backend_unavailable", "error": str(exc), "mode": payload.get("mode") or "bidirectional", "degraded": True, "suggestion": "API 异常已降级到只读建议模式"}

    def _notes_organize(self, payload: dict[str, Any]) -> dict[str, Any]:
        max_items = max(1, min(int(payload.get("max_items") or 20), 100))
        notes = self.note_repository.list_notes(include_deleted=False)[:max_items]
        bucket: dict[str, int] = {}
        for note in notes:
            tags = note.get("tags") or []
            if isinstance(tags, list) and tags:
                key = str(tags[0]).strip() or "未分类"
            else:
                key = str(note.get("category") or "未分类").strip() or "未分类"
            bucket[key] = bucket.get(key, 0) + 1
        summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "total_notes": len(notes), "buckets": bucket}
        event_entry = {"event": "notes.organized", "time": datetime.now(timezone.utc).isoformat(), "payload": summary}
        self.data_store.update_json(
            "assistant_events",
            [],
            lambda history: (list(history) if isinstance(history, list) else [])[-199:] + [event_entry],
        )
        return summary

    def _usage_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        top_n = max(3, min(int(payload.get("top_n") or 10), 30))
        snapshot = self.environment_probe.usage_snapshot(top_n=top_n)
        self.data_store.update_json(
            "assistant_usage_stats",
            [],
            lambda history: (list(history) if isinstance(history, list) else [])[-499:] + [snapshot],
        )
        return snapshot

    def _upgrade_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        channel = str(payload.get("channel") or self.settings_repository.get_setting("assistant_upgrade_channel", "stable")).strip() or "stable"
        allow_self_update = bool(self.settings_repository.get_setting(KEY_ASSISTANT_ALLOW_SELF_UPDATE, False))
        return {
            "channel": channel,
            "allow_self_update": allow_self_update,
            "allowed_update_actions": ["rule_update", "config_update", "plugin_install"],
            "forbidden_targets": ["core_python_files", "main_window_runtime", "database_schema_without_review"],
            "recommendation": "先更新规则、配置或插件；核心代码仍需人工审查后再做 smoke/regression 与灰度发布。",
            "next_action": "manual_review_required",
        }

    def _safe_download_filename(self, filename: str) -> str:
        cleaned = _re.sub(r"[^A-Za-z0-9.\-_一-龯]+", "_", str(filename or "download").strip()).strip("._")
        return cleaned or "download"
