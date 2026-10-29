"""行为日志领域模型 — BehaviorEvent。"""

from __future__ import annotations

from pydantic import BaseModel, Field

PRIVACY_LOCAL = "local"
PRIVACY_SYNCED = "synced"

CATEGORY_FILE = "file"
CATEGORY_BROWSER = "browser"
CATEGORY_APP = "app"
CATEGORY_SYSTEM = "system"
CATEGORY_INPUT = "input"

SOURCE_WATCHER = "watcher"
SOURCE_BROWSER_BRIDGE = "browser_bridge"
SOURCE_APP_HOOK = "app_hook"
SOURCE_MANUAL = "manual"

FILE_ACTIONS = frozenset({"file_created", "file_modified", "file_deleted", "file_moved", "file_opened"})
BROWSER_ACTIONS = frozenset({"page_visited", "page_updated", "tab_focused", "tab_closed", "search_queried"})
APP_ACTIONS = frozenset({"app_focus", "app_blur", "app_startup", "app_shutdown", "screen_changed"})
SYSTEM_ACTIONS = frozenset({"sleep", "wake", "idle", "active", "settings_changed"})
INPUT_ACTIONS = frozenset({"text_copied", "file_dropped", "voice_input"})


class BehaviorEvent(BaseModel):
    """行为日志事件 — 标准格式，用于所有行为日志的记录、存储和同步。"""

    id: str = ""
    timestamp: str = ""
    session_id: str = ""

    category: str = CATEGORY_APP
    action: str = ""

    detail: dict = Field(default_factory=dict)

    source: str = SOURCE_APP_HOOK
    privacy: str = PRIVACY_LOCAL

    duration_ms: int | None = None
