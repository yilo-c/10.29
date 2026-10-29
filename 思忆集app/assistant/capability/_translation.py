"""Translation and external service integration capability domain.

Extracted from capability_router.py — TranslationCapabilityDomain.
"""
from __future__ import annotations

import logging
from typing import Any

from services.translate_service import create_youdao_translate_api

logger = logging.getLogger("siyi.assistant.capability.translation")

try:
    from adapters.todoist import TodoistAdapter
    _HAS_TODOIST_ADAPTER = True
except ImportError:
    TodoistAdapter = None  # type: ignore
    _HAS_TODOIST_ADAPTER = False

try:
    from adapters.youdao_note import YoudaoNoteAdapter
    _HAS_YOUDAO_ADAPTER = True
except ImportError:
    YoudaoNoteAdapter = None  # type: ignore
    _HAS_YOUDAO_ADAPTER = False


class TranslationCapabilityDomain:
    """Translation, Youdao Note, and Todoist integration capabilities."""

    def __init__(self, router) -> None:
        self.router = router

    # ── Translation ───────────────────────────────────────────────────

    def translate_text(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        if not text:
            return {"success": False, "reason": "no_text"}
        from_lang = str(payload.get("from") or "auto")
        to_lang = str(payload.get("to") or "zh-CHS")
        try:
            api = create_youdao_translate_api(settings_repository=self.router.settings_repository)
            if not api.is_configured():
                return {"success": False, "reason": "translate_not_configured",
                        "hint": "请在设置中配置有道翻译 API Key"}
            result = api.translate(text, from_lang, to_lang)
            return {"success": True, "result": result, "from": from_lang, "to": to_lang}
        except Exception as exc:
            logger.warning("translate.text failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    # ── Todoist 任务适配器 ───────────────────────────────────────────

    def _get_todoist_adapter(self) -> TodoistAdapter | None:
        if not _HAS_TODOIST_ADAPTER:
            return None
        try:
            return TodoistAdapter(settings_repository=self.router.settings_repository)
        except Exception:
            logger.debug("todoist adapter init failed", exc_info=True)
            return None

    def todoist_task_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapter = self._get_todoist_adapter()
        if adapter is None:
            return {"success": False, "reason": "todoist_adapter_unavailable"}
        if not adapter.is_configured():
            return {"success": False, "reason": "todoist_not_configured",
                    "hint": "请先在设置中配置 Todoist API Token"}
        project_id = str(payload.get("project_id") or "").strip() or None
        limit = int(payload.get("limit") or 20)
        try:
            return adapter.list_tasks(project_id=project_id, limit=limit)
        except Exception as exc:
            logger.warning("todoist.task.list failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def todoist_task_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapter = self._get_todoist_adapter()
        if adapter is None:
            return {"success": False, "reason": "todoist_adapter_unavailable"}
        content = str(payload.get("content") or "").strip()
        if not content:
            return {"success": False, "reason": "missing_content"}
        description = str(payload.get("description") or "").strip()
        project_id = str(payload.get("project_id") or "").strip() or None
        priority = int(payload.get("priority") or 1)
        due_string = str(payload.get("due_string") or "").strip() or None
        try:
            return adapter.create_task(
                content=content, description=description,
                project_id=project_id, priority=priority, due_string=due_string,
            )
        except Exception as exc:
            logger.warning("todoist.task.create failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def todoist_project_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapter = self._get_todoist_adapter()
        if adapter is None:
            return {"success": False, "reason": "todoist_adapter_unavailable"}
        try:
            return adapter.list_projects()
        except Exception as exc:
            logger.warning("todoist.project.list failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    # ── 有道云笔记适配器 ────────────────────────────────────────────

    def _get_youdao_adapter(self) -> YoudaoNoteAdapter | None:
        if not _HAS_YOUDAO_ADAPTER:
            return None
        try:
            return YoudaoNoteAdapter(
                oauth_provider=None,
                integration_hub=self.router.integration_hub,
                provider_registry=getattr(self.router, "source_registry", None),
                settings_repository=self.router.settings_repository,
            )
        except Exception:
            logger.debug("youdao adapter init failed", exc_info=True)
            return None

    def youdao_note_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapter = self._get_youdao_adapter()
        if adapter is None:
            return {"success": False, "reason": "youdao_adapter_unavailable"}
        if not adapter.is_authorized():
            return {"success": False, "reason": "youdao_not_authorized",
                    "hint": "请先在设置中完成有道云 OAuth 授权"}
        limit = int(payload.get("limit") or 20)
        offset = int(payload.get("offset") or 0)
        try:
            result = adapter.list_notes(limit=limit, offset=offset)
            return {"success": True, "notes": result.get("items", result), "count": len(result.get("items", result))}
        except Exception as exc:
            logger.warning("youdao.note.list failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def youdao_note_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapter = self._get_youdao_adapter()
        if adapter is None:
            return {"success": False, "reason": "youdao_adapter_unavailable"}
        title = str(payload.get("title") or "").strip()
        content = str(payload.get("content") or "").strip()
        if not title:
            return {"success": False, "reason": "missing_title"}
        folder = str(payload.get("folder") or "").strip() or None
        try:
            result = adapter.create_note(title=title, content=content, folder=folder)
            return {"success": True, "note": result}
        except Exception as exc:
            logger.warning("youdao.note.create failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def youdao_note_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapter = self._get_youdao_adapter()
        if adapter is None:
            return {"success": False, "reason": "youdao_adapter_unavailable"}
        note_id = str(payload.get("note_id") or "").strip()
        if not note_id:
            return {"success": False, "reason": "missing_note_id"}
        try:
            result = adapter.get_note(note_id)
            return {"success": True, "note": result}
        except Exception as exc:
            logger.warning("youdao.note.get failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def youdao_note_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapter = self._get_youdao_adapter()
        if adapter is None:
            return {"success": False, "reason": "youdao_adapter_unavailable"}
        note_id = str(payload.get("note_id") or "").strip()
        if not note_id:
            return {"success": False, "reason": "missing_note_id"}
        title = str(payload.get("title") or "").strip() or None
        content = str(payload.get("content") or "").strip() or None
        if not title and not content:
            return {"success": False, "reason": "nothing_to_update"}
        try:
            result = adapter.update_note(note_id, title=title, content=content)
            return {"success": True, "note": result}
        except Exception as exc:
            logger.warning("youdao.note.update failed: %s", exc)
            return {"success": False, "reason": str(exc)}
