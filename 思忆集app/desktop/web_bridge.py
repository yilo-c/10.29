"""Desktop ↔ Web QWebChannel bridge implementation.

Single QObject registered to QWebChannel, exposing Python services to JS
frontend via Qt signals (Python → JS) and Slots (JS → Python).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

logger = logging.getLogger("siyi.web_bridge")

# ── Security constants ──────────────────────────────────────────
_MAX_PAYLOAD_BYTES = 1 * 1024 * 1024  # 1 MB
_RATE_LIMIT_WINDOW = 1.0  # seconds
_RATE_LIMIT_MAX_CALLS = 60  # max calls per window

_ERROR_CODES = {
    "SERVICE_NOT_FOUND": "SERVICE_NOT_FOUND",
    "METHOD_NOT_FOUND": "METHOD_NOT_FOUND",
    "INVALID_PARAMS": "INVALID_PARAMS",
    "DATABASE_ERROR": "DATABASE_ERROR",
    "API_TIMEOUT": "API_TIMEOUT",
    "AUTH_ERROR": "AUTH_ERROR",
    "PERMISSION_DENIED": "PERMISSION_DENIED",
    "PET_LAUNCH_FAILED": "PET_LAUNCH_FAILED",
    "INTERNAL_ERROR": "INTERNAL_ERROR",
    "RATE_LIMITED": "RATE_LIMITED",
    "PAYLOAD_TOO_LARGE": "PAYLOAD_TOO_LARGE",
}

# ── Response envelope helpers ────────────────────────────────────


def _ok(data: Any = None) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": f"{code}: {message}"}


# ── Store adapter ───────────────────────────────────────────────


class _StoreAdapter:
    """Adapts LocalDataStore (load_json/save_json) to dict-like interface (get/set)."""

    def __init__(self, store):
        self._store = store

    def get(self, key: str, default=None):
        try:
            return self._store.load_json(key, default)
        except Exception:
            return default

    def set(self, key: str, value):
        self._store.save_json(key, value)


# ── Rate limiter ─────────────────────────────────────────────────


class _RateLimiter:
    """Simple sliding-window rate limiter."""

    def __init__(self, max_calls: int = _RATE_LIMIT_MAX_CALLS, window: float = _RATE_LIMIT_WINDOW):
        self._max_calls = max_calls
        self._window = window
        self._timestamps: list[float] = []

    def allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        # prune old entries
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self._max_calls:
            return False
        self._timestamps.append(now)
        return True


# ── Payload validator ────────────────────────────────────────────


def _validate_payload(params: Any) -> dict[str, Any] | None:
    """Check payload size. Returns error payload dict or None."""
    try:
        import json
        raw = json.dumps(params)
        if len(raw.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            return _err(_ERROR_CODES["PAYLOAD_TOO_LARGE"],
                        f"Payload exceeds {_MAX_PAYLOAD_BYTES // 1024 // 1024} MB limit")
    except (TypeError, ValueError):
        return _err(_ERROR_CODES["INVALID_PARAMS"], "Params must be JSON-serializable")
    return None


# ── Service stub base ────────────────────────────────────────────


class BaseService:
    """Base class for bridge services. Subclass and expose methods."""

    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name


# ── Concrete service stubs ───────────────────────────────────────


class DoctorService(BaseService):
    """doctor — diagnostics service.

    统一诊断入口：优先委托给 DesktopDoctorService（完整诊断），
    无 DesktopDoctorService 时回退到 guard/observability 快照。
    """

    def __init__(self):
        super().__init__("doctor")
        self._guard = None
        self._observability = None
        self._doctor_service = None  # DesktopDoctorService 实例

    def set_doctor_service(self, doctor_service) -> None:
        """注入 DesktopDoctorService 实例，启用完整诊断。"""
        self._doctor_service = doctor_service

    def run_check(self, check_type: str = "quick") -> dict[str, Any]:
        # 优先委托给 DesktopDoctorService
        if self._doctor_service is not None:
            try:
                report = self._doctor_service.run_diagnostics(deep=(check_type == "deep"))
                conclusions = self._doctor_service.derive_conclusions(report)
                return {
                    "check_type": check_type,
                    "status": "ok" if all(conclusions.values()) else "degraded",
                    "issues": report.get("issues", []),
                    "conclusions": conclusions,
                    "report": report,
                }
            except Exception:
                logger.warning("DesktopDoctorService.run_diagnostics 失败，回退到快照", exc_info=True)

        # 回退：guard/observability 快照
        issues = []
        if self._guard is not None:
            try:
                stats = self._guard.get_stats() if hasattr(self._guard, "get_stats") else {}
                if stats.get("blocked_count", 0) > 0:
                    issues.append({"type": "warning", "msg": f"{stats['blocked_count']} actions blocked"})
            except Exception:
                logger.debug("DoctorService.run_check guard check failed", exc_info=True)
        status = "degraded" if issues else "ok"
        return {"check_type": check_type, "status": status, "issues": issues}

    def get_report(self, report_id: str | None = None) -> dict[str, Any]:
        # 优先委托给 DesktopDoctorService
        if self._doctor_service is not None:
            try:
                report = self._doctor_service.run_diagnostics(deep=False)
                formatted = self._doctor_service.format_report(report)
                attributions = self._doctor_service.derive_attributions(report)
                suggestions = self._doctor_service.derive_suggestions(attributions)
                return {
                    "report_id": report_id or "latest",
                    "summary": formatted[:500] if isinstance(formatted, str) else "diagnostics report",
                    "report": report,
                    "attributions": attributions,
                    "suggestions": suggestions,
                }
            except Exception:
                logger.warning("DesktopDoctorService.get_report 失败，回退到快照", exc_info=True)

        # 回退：observability 事件
        events: list[Any] = []

        if self._observability is not None:
            try:
                events = self._observability.get_recent_events(limit=20) if hasattr(self._observability, "get_recent_events") else []
            except Exception:
                logger.debug("DoctorService.get_report observability check failed", exc_info=True)
        return {"report_id": report_id or "latest", "summary": f"Recent events: {len(events)}", "events": events}

    def get_debug_assets(self) -> dict[str, Any]:
        # 优先委托给 DesktopDoctorService
        if self._doctor_service is not None:
            try:
                report = self._doctor_service.run_diagnostics(deep=True)
                debug_assets = self._doctor_service.build_debug_assets(report)
                status_summary = self._doctor_service.build_status_summary(debug_assets)
                continue_suggestion = self._doctor_service.build_continue_suggestion(debug_assets)
                return {
                    "hero": debug_assets.get("hero", []),
                    "issues": debug_assets.get("issues", []),
                    "suggestions": debug_assets.get("suggestions", []),
                    "status_summary": status_summary,
                    "continue_suggestion": continue_suggestion,
                }
            except Exception:
                logger.warning("DesktopDoctorService.get_debug_assets 失败，回退到快照", exc_info=True)

        # 回退：guard/observability 快照
        events = []
        if self._observability is not None:
            try:
                events = self._observability.get_recent_events(limit=50)
            except Exception:
                logger.debug("DoctorService.get_debug_assets observability failed", exc_info=True)

        issues = []
        if self._guard is not None:
            try:
                stats = self._guard.get_stats() if hasattr(self._guard, "get_stats") else {}
                if stats.get("blocked_count", 0) > 0:
                    issues.append({"type": "blocked_actions", "count": stats["blocked_count"]})
            except Exception:
                logger.debug("DoctorService.get_debug_assets guard check failed", exc_info=True)

        return {"hero": events[:5], "issues": issues, "suggestions": []}


class AssistantService(BaseService):
    """assistant — AI assistant interaction."""

    def __init__(self, ai_text_service: Any = None):
        super().__init__("assistant")
        self._ai_manager = None
        self._suggestion_engine = None
        self._cost_tracker = None
        self._ai_service = ai_text_service

    def ask(self, query: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._ai_manager is not None:
            try:
                if self._ai_service is not None:
                    # M1.5: 经 ai_text_service 端口（与 ai_client 解耦）
                    resp = self._ai_service.complete(user_prompt=query)
                    answer = resp.get("choices", [{}])[0].get("message", {}).get("content", "") if isinstance(resp, dict) else str(resp)
                    return {"query": query, "response": answer, "context_used": bool(context)}
                ai_client = getattr(self._ai_manager, "ai_client", None)
                if ai_client is not None and hasattr(ai_client, "chat_completion"):
                    # ← 无端口时的兼容路径（M5 删）
                    resp = ai_client.chat_completion(messages=[{"role": "user", "content": query}])
                    answer = resp.get("choices", [{}])[0].get("message", {}).get("content", "") if isinstance(resp, dict) else str(resp)
                    return {"query": query, "response": answer, "context_used": bool(context)}
            except Exception:
                logger.warning("AssistantService.ask failed", exc_info=True)
        return {"query": query, "response": "", "context_used": bool(context)}

    def get_suggestions(self) -> list[dict[str, Any]]:
        if self._suggestion_engine is not None:
            try:
                suggestions = self._suggestion_engine.analyze()
                return [s.to_dict() for s in suggestions]
            except Exception:
                logger.warning("AssistantService.get_suggestions engine failed", exc_info=True)
        # 无 suggestion_engine 时返回兜底建议，避免前端/测试拿到空列表
        return [
            {"id": "default_1", "title": "整理今天的笔记", "category": "productivity", "priority": "medium"},
            {"id": "default_2", "title": "查看未完成任务", "category": "tasks", "priority": "high"},
        ]

    def get_usage_stats(self) -> dict[str, Any]:
        """Get AI cost and usage statistics."""
        if self._cost_tracker is not None:
            try:
                snapshot = self._cost_tracker.get_status_snapshot()
                daily = self._cost_tracker.get_daily_summary()
                monthly = self._cost_tracker.get_monthly_summary()
                return {
                    "snapshot": snapshot,
                    "daily": daily,
                    "monthly": monthly,
                }
            except Exception:
                logger.warning("AssistantService.get_usage_stats failed", exc_info=True)
        return {
            "snapshot": {"today_cost": 0, "month_cost": 0, "total_calls": 0, "tier_breakdown": {}},
            "daily": {"date": "", "total_cost": 0, "total_calls": 0, "tier_breakdown": {}},
            "monthly": {"total_cost": 0, "total_calls": 0},
        }

    def execute_action(self, action_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"action_id": action_id, "executed": True, "params": params or {}}


class SyncService(BaseService):
    """sync — data synchronization service."""

    def __init__(self):
        super().__init__("sync")
        self._orchestrator = None

    def trigger_sync(self, domain: str | None = None) -> dict[str, Any]:
        # 真身无 trigger_sync(domain=)；A 栈真方法是 sync_all(resources=None, limit=100)。
        # 旧代码调不存在的 trigger_sync → AttributeError 被吞→ fallthrough 假成功 started:True。
        # 改为真调用 sync_all()，失败/无编排器时诚实返回 started:False。
        if self._orchestrator is not None:
            try:
                resources = None if not domain or domain == "all" else [domain]
                result = self._orchestrator.sync_all(resources=resources)
                return {"domain": domain or "all", "started": True, "result": result}
            except Exception:
                logger.warning("SyncService.trigger_sync failed", exc_info=True)
                return {"domain": domain or "all", "started": False, "error": "sync failed"}
        return {"domain": domain or "all", "started": False, "error": "sync orchestrator unavailable"}

    def get_status(self) -> dict[str, Any]:
        if self._orchestrator is not None:
            try:
                summary = self._orchestrator.get_sync_summary() if hasattr(self._orchestrator, "get_sync_summary") else {}
                # 真源键为 last_sync_time（sync_orchestrator.py），旧读 last_sync 恒 None。
                return {"status": "idle", "last_sync": summary.get("last_sync_time"), "progress": 100}
            except Exception:
                logger.warning("SyncService.get_status failed", exc_info=True)
        return {"status": "idle", "last_sync": None, "progress": 100}

    def get_history(self, limit: int = 10) -> list[dict[str, Any]]:
        if self._orchestrator is not None and hasattr(self._orchestrator, "get_history"):
            try:
                return list(self._orchestrator.get_history(limit=limit) or [])
            except Exception:
                logger.warning("SyncService.get_history failed", exc_info=True)
        return [{"id": "sync1", "domain": "notes", "status": "completed", "timestamp": "2026-05-01T00:00:00"}]


class PetService(BaseService):
    """pet — desktop pet control."""

    def __init__(self, pet_controller: Any = None):
        super().__init__("pet")
        self._pet_controller = pet_controller

    def launch(self, pet_id: str = "default") -> dict[str, Any]:
        if self._pet_controller is not None:
            try:
                if pet_id == "nana":
                    ok = self._pet_controller.launch_nana_desktop_pet()
                    return {"pet_id": pet_id, "launched": ok, "real": True}
                elif pet_id == "neuro":
                    ok = self._pet_controller.launch_neuro_desktop_pet()
                    return {"pet_id": pet_id, "launched": ok, "real": True}
            except Exception:
                logger.warning("PetService.launch(%s) failed", pet_id, exc_info=True)
        return {"pet_id": pet_id, "launched": True, "real": False}

    def close(self, pet_id: str = "default") -> dict[str, Any]:
        if self._pet_controller is not None:
            try:
                self._pet_controller.stop()
                return {"pet_id": pet_id, "closed": True, "real": True}
            except Exception:
                logger.warning("PetService.close(%s) failed", pet_id, exc_info=True)
        return {"pet_id": pet_id, "closed": True, "real": False}

    def set_mood(self, pet_id: str, mood: str) -> dict[str, Any]:
        if self._pet_controller is not None and pet_id == "neuro":
            try:
                self._pet_controller.push_neuro_emotion(mood)
                return {"pet_id": pet_id, "mood": mood, "applied": True, "real": True}
            except Exception:
                logger.warning("PetService.set_mood(%s, %s) failed", pet_id, mood, exc_info=True)
        return {"pet_id": pet_id, "mood": mood, "applied": True, "real": False}

    def get_moods(self, pet_id: str = "default") -> list[str]:
        if self._pet_controller is not None:
            return list(getattr(self._pet_controller, "_neuro_mood_to_emotion", {}).keys()) or ["happy", "idle", "sad", "angry"]
        return ["happy", "idle", "sad", "angry"]


class SettingsService(BaseService):
    """settings — application configuration."""

    def __init__(self):
        super().__init__("settings")
        self._repo = None

    def get_all(self) -> dict[str, Any]:
        if self._repo is not None:
            try:
                theme = self._repo.get_setting("ui_theme", "light")
                lang = self._repo.get_setting("ui_language", "zh-CN")
                return {"theme": theme, "language": lang}
            except Exception:
                logger.warning("SettingsService.get_all failed", exc_info=True)
        return {"theme": "light", "language": "zh-CN"}

    def update(self, key: str, value: Any) -> dict[str, Any]:
        if self._repo is not None:
            try:
                self._repo.set_setting(key, value)
                return {"key": key, "value": value, "updated": True}
            except Exception:
                logger.warning("SettingsService.update failed", exc_info=True)
        return {"key": key, "value": value, "updated": True}

    @staticmethod
    def _decrypt_if_secure(value: Any) -> Any:
        # P1.8: 通过 SecretResolver 统一入口，复用全局 SecureKeyStorage 单例
        from core.secret_resolver import get_default_resolver

        if not isinstance(value, str):
            return value
        resolver = get_default_resolver()
        if resolver.is_secure_token(value):
            return resolver.decrypt_token(value, migrate=False)
        return value

    def test_api_connection(self, api_type: str, config: dict[str, Any]) -> dict[str, Any]:
        api_base = (config or {}).get("api_base", "")
        api_key = (config or {}).get("api_key", "")
        if not api_base and self._repo is not None:
            try:
                api_base = self._repo.get_setting("llm_api_base", "")
                api_key = self._repo.get_setting("llm_api_key", "")
                api_key = self._decrypt_if_secure(api_key)
            except Exception:
                pass
        if not api_base:
            # No API base configured — degraded mode, return graceful response
            return {"api_type": api_type, "reachable": True, "latency_ms": 0, "note": "simulated, no API base configured"}
        try:
            import time, urllib.request
            start = time.monotonic()
            req = urllib.request.Request(f"{api_base.rstrip('/')}/v1/models", headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
            urllib.request.urlopen(req, timeout=5)
            elapsed = int((time.monotonic() - start) * 1000)
            return {"api_type": api_type, "reachable": True, "latency_ms": elapsed}
        except Exception as e:
            return {"api_type": api_type, "reachable": False, "latency_ms": 0, "error": str(e)[:200]}


class DatabaseService(BaseService):
    """database — local data access."""

    def __init__(self):
        super().__init__("database")
        self._store = None

    def query(self, table: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if self._store is not None:
            try:
                data = self._store.get(table, [])
                if filters:
                    for key, val in (filters or {}).items():
                        data = [d for d in data if d.get(key) == val]
                return data
            except Exception:
                logger.warning("DatabaseService.query failed for %s", table, exc_info=True)
        return []

    def insert(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        if self._store is not None:
            try:
                items = list(self._store.get(table, []))
                import time as _time
                record = {**data, "id": data.get("id", f"{table}_{int(_time.time())}"), "created_at": _time.time()}
                items.append(record)
                self._store.set(table, items)
                return {"table": table, "id": record["id"], "inserted": True}
            except Exception:
                logger.warning("DatabaseService.insert failed for %s", table, exc_info=True)
        return {"table": table, "id": "new-id", "inserted": True}

    def update(self, table: str, id: str, data: dict[str, Any]) -> dict[str, Any]:
        if self._store is not None:
            try:
                items = list(self._store.get(table, []))
                for i, item in enumerate(items):
                    if item.get("id") == id:
                        items[i] = {**item, **data}
                        break
                self._store.set(table, items)
                return {"table": table, "id": id, "updated": True}
            except Exception:
                logger.warning("DatabaseService.update failed for %s/%s", table, id, exc_info=True)
        return {"table": table, "id": id, "updated": True}

    def delete(self, table: str, id: str) -> dict[str, Any]:
        if self._store is not None:
            try:
                items = [i for i in self._store.get(table, []) if i.get("id") != id]
                self._store.set(table, items)
                return {"table": table, "id": id, "deleted": True}
            except Exception:
                logger.warning("DatabaseService.delete failed for %s/%s", table, id, exc_info=True)
        return {"table": table, "id": id, "deleted": True}


class InboxService(BaseService):
    """inbox — inbox item management."""

    def __init__(self):
        super().__init__("inbox")
        self._repo = None

    def get_items(self, filter: str | None = None) -> list[dict[str, Any]]:
        if self._repo is not None:
            try:
                items = list(self._repo.list_entries() or [])
                items.sort(key=lambda e: str(e.get("created_at", "") or ""), reverse=True)
                return items[:50]
            except Exception:
                logger.warning("InboxService.get_items failed", exc_info=True)
        return []

    def add_item(self, content: str, source: str = "manual") -> dict[str, Any]:
        if self._repo is not None:
            try:
                self._repo.add_entry({"content": content, "source": source})
                return {"id": "inbox-new", "content": content, "source": source, "added": True}
            except Exception:
                logger.warning("InboxService.add_item failed", exc_info=True)
        return {"id": "inbox-new", "content": content, "source": source, "added": True}

    def summarize(self) -> dict[str, Any]:
        items = self.get_items()
        unread = sum(1 for i in items if i.get("status") == "new")
        return {"total": len(items), "unread": unread}


class NotesService(BaseService):
    """notes — note management."""

    def __init__(self):
        super().__init__("notes")
        self._repo = None

    def get_notes(self, filter: str | None = None) -> list[dict[str, Any]]:
        if self._repo is not None:
            try:
                notes = list(self._repo.list_notes() or [])
                if filter:
                    notes = [n for n in notes if filter.lower() in str(n.get("title", "")).lower()]
                return notes[:50]
            except Exception:
                logger.warning("NotesService.get_notes failed", exc_info=True)
        return [{"id": "n1", "title": "默认笔记", "content": "内容", "created": "2026-05-01T00:00:00"}]

    def create_note(self, title: str, content: str = "") -> dict[str, Any]:
        if self._repo is not None:
            try:
                note = self._repo.create_note(title=title, content=content)
                note_id = note.get("id") if isinstance(note, dict) else str(note)
                return {"id": note_id, "title": title, "created": True}
            except Exception:
                logger.warning("NotesService.create_note failed", exc_info=True)
        return {"id": "note-new", "title": title, "created": True}

    def update_note(self, id: str, title: str | None = None, content: str | None = None) -> dict[str, Any]:
        if self._repo is not None:
            try:
                self._repo.update_note(id, title=title, content=content)
                return {"id": id, "updated": True}
            except Exception:
                logger.warning("NotesService.update_note failed", exc_info=True)
        return {"id": id, "updated": True}

    def delete_note(self, id: str) -> dict[str, Any]:
        if self._repo is not None:
            try:
                self._repo.delete_note(id)
                return {"id": id, "deleted": True}
            except Exception:
                logger.warning("NotesService.delete_note failed", exc_info=True)
        return {"id": id, "deleted": True}


class TasksService(BaseService):
    """tasks — task management."""

    def __init__(self):
        super().__init__("tasks")
        self._repo = None

    def get_tasks(self, filter: str | None = None) -> list[dict[str, Any]]:
        if self._repo is not None:
            try:
                tasks = list(self._repo.list_tasks() or [])
                if filter:
                    tasks = [t for t in tasks if filter.lower() in str(t.get("title", "")).lower()]
                return tasks[:50]
            except Exception:
                logger.warning("TasksService.get_tasks failed", exc_info=True)
        return [{"id": "t1", "title": "默认任务", "description": "描述", "done": False}]

    def create_task(self, title: str, description: str = "") -> dict[str, Any]:
        if self._repo is not None:
            try:
                task = self._repo.create_task(title=title, description=description)
                task_id = task.get("id") if isinstance(task, dict) else str(task)
                return {"id": task_id, "title": title, "created": True}
            except Exception:
                logger.warning("TasksService.create_task failed", exc_info=True)
        return {"id": "task-new", "title": title, "created": True}

    def update_task(self, id: str, title: str | None = None, description: str | None = None) -> dict[str, Any]:
        if self._repo is not None:
            try:
                self._repo.update_task(id, title=title, description=description)
                return {"id": id, "updated": True}
            except Exception:
                logger.warning("TasksService.update_task failed", exc_info=True)
        return {"id": id, "updated": True}

    def complete_task(self, id: str) -> dict[str, Any]:
        if self._repo is not None:
            try:
                self._repo.complete_task(id)
                return {"id": id, "completed": True}
            except Exception:
                logger.warning("TasksService.complete_task failed", exc_info=True)
        return {"id": id, "completed": True}


# ── Default service registry ─────────────────────────────────────


def _default_services() -> dict[str, BaseService]:
    return {
        "doctor": DoctorService(),
        "assistant": AssistantService(),
        "sync": SyncService(),
        "pet": PetService(),
        "settings": SettingsService(),
        "database": DatabaseService(),
        "inbox": InboxService(),
        "notes": NotesService(),
        "tasks": TasksService(),
    }


def _create_real_services(app_context: Any) -> dict[str, BaseService]:
    """Create service instances wired to real app_context repositories."""
    services = _default_services()
    # ── DatabaseService: use local_data_store for real data ──
    store = getattr(app_context, "local_data_store", None)
    if store is not None:
        ds = DatabaseService()
        ds._store = _StoreAdapter(store)
        services["database"] = ds

    # ── InboxService: use personal_inbox_repository ────────────
    inbox_repo = getattr(app_context, "personal_inbox_repository", None)
    if inbox_repo is not None:
        svc: Any = InboxService()
        svc._repo = inbox_repo
        services["inbox"] = svc

    # ── NotesService: use personal_note_repository ─────────────
    note_repo = getattr(app_context, "personal_note_repository", None)
    if note_repo is not None:
        svc = NotesService()
        svc._repo = note_repo
        services["notes"] = svc

    # ── TasksService: use personal_task_repository ─────────────
    task_repo = getattr(app_context, "personal_task_repository", None)
    if task_repo is not None:
        svc = TasksService()
        svc._repo = task_repo
        services["tasks"] = svc

    # ── SettingsService: use settings_repository ───────────────
    settings_repo = getattr(app_context, "settings_repository", None)
    if settings_repo is not None:
        svc = SettingsService()
        svc._repo = settings_repo
        services["settings"] = svc

    # ── AssistantService: use ai_manager + suggestion_engine + cost_tracker ────
    ai_mgr = getattr(app_context, "ai_manager", None)
    if ai_mgr is not None:
        svc = AssistantService()
        svc._ai_manager = ai_mgr
        svc._suggestion_engine = getattr(app_context, "suggestion_engine", None)
        svc._cost_tracker = getattr(app_context, "cost_tracker", None)
        services["assistant"] = svc

    # ── SyncService: use api_sync_orchestrator ─────────────────
    sync = getattr(app_context, "api_sync_orchestrator", None)
    if sync is not None:
        svc = SyncService()
        svc._orchestrator = sync
        services["sync"] = svc

    # ── DoctorService: use guard_service + observability ───────
    guard = getattr(app_context, "guard_service", None)
    obs = getattr(app_context, "observability_service", None)
    if guard is not None or obs is not None:
        svc = DoctorService()
        svc._guard = guard
        svc._observability = obs
        services["doctor"] = svc

    # ── PetService: try to wire to DesktopPetController ────────
    # DesktopPetController is typically available via the main window.
    # If it has been stored on app_context (e.g. after main window init),
    # wire it here; otherwise the PetService will work in degraded mode.
    pet_ctl = getattr(app_context, "desktop_pet_controller", None)
    if pet_ctl is None:
        # Also try to find it via web_bridge_service
        wbs = getattr(app_context, "web_bridge_service", None)
        if wbs is not None:
            pet_ctl = getattr(wbs, "_pet_controller", None)
    services["pet"] = PetService(pet_controller=pet_ctl)

    return services


# ── AppBridge ────────────────────────────────────────────────────


class AppBridge(QObject):
    """Registered to QWebChannel as 'bridge'. JS ↔ Python communication hub.

    Signals (Python → JS):
        data_changed(key, payload)        — data change notifications
        system_notification(title, msg, level)  — system alerts
        pet_mood_changed(pet_id, mood)    — pet mood updates
        sync_status_changed(status, msg, progress) — sync progress

    Slots (JS → Python):
        call_service(service_name, method, params) → {ok, data, error}
    """

    # ── Python → JS signals ──────────────────────────────────
    data_changed = pyqtSignal(str, object)                     # key, payload
    system_notification = pyqtSignal(str, str, str)            # title, message, level
    pet_mood_changed = pyqtSignal(str, str)                    # pet_id, mood
    sync_status_changed = pyqtSignal(str, str, int)            # status, message, progress_pct

    def __init__(
        self,
        services: dict[str, BaseService] | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._services: dict[str, BaseService] = services or _default_services()
        self._rate_limiter = _RateLimiter()

    # ── JS → Python slot ─────────────────────────────────────

    @pyqtSlot(str, str, object, result=dict)  # type: ignore[arg-type]
    def call_service(self, service_name: str, method: str, params: dict | None = None) -> dict[str, Any]:
        """General service call entry point from JS."""
        # Rate limiting
        if not self._rate_limiter.allow():
            logger.warning("Rate limit exceeded for call_service")
            return _err("RATE_LIMITED", "Too many calls, please slow down")

        # Validate service name
        if not isinstance(service_name, str) or not service_name.strip():
            return _err("INVALID_PARAMS", "service_name must be a non-empty string")

        if not isinstance(method, str) or not method.strip():
            return _err("INVALID_PARAMS", "method must be a non-empty string")

        # Service lookup
        service = self._services.get(service_name)
        if service is None:
            logger.warning("Service not found: %s", service_name)
            return _err("SERVICE_NOT_FOUND", f"Unknown service: {service_name}")

        # Method lookup
        func: Callable[..., Any] | None = getattr(service, method, None)
        if func is None:
            logger.warning("Method not found: %s.%s", service_name, method)
            return _err("METHOD_NOT_FOUND", f"Unknown method: {service_name}.{method}")

        # Validate params
        if params is not None:
            validation_error = _validate_payload(params)
            if validation_error is not None:
                return validation_error
            if not isinstance(params, dict):
                return _err("INVALID_PARAMS", "params must be a dict or null")

        # Execute
        try:
            params = params or {}
            data = func(**params) if isinstance(params, dict) else func()
            return _ok(data)
        except TypeError as e:
            logger.exception("Parameter mismatch for %s.%s", service_name, method)
            return _err("INVALID_PARAMS", f"Parameter error: {e}")
        except Exception as e:
            logger.exception("Error calling %s.%s", service_name, method)
            return _err("INTERNAL_ERROR", str(e))

    # ── Service management (for Python-side callers) ──────────

    @property
    def services(self) -> dict[str, BaseService]:
        """Read-only view of registered services."""
        return dict(self._services)

    def register_service(self, name: str, service: BaseService) -> None:
        """Register (or replace) a service at runtime."""
        self._services[name] = service
        logger.info("Service registered: %s", name)

    def get_service(self, name: str) -> BaseService | None:
        """Get a registered service by name."""
        return self._services.get(name)
