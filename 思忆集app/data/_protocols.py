"""数据层接口定义。

定义 data/ 层对外暴露的契约接口（Protocol），
上层（services/ui）应依赖这些接口而非具体实现类。

与 repositories/_protocols.py 对齐，避免重复定义。
若已有同功能接口则直接引用。
"""

from __future__ import annotations

from typing import Any, Protocol


class StorageContextPort(Protocol):
    """笔记存储接口 — 对应 PersonalNoteRepository 能力子集。"""

    def get_note(self, note_id: int) -> dict[str, Any] | None:
        ...

    def save_note(self, note_info: dict[str, Any]) -> int:
        """新增或保存笔记，返回笔记 ID。"""
        ...

    def delete_note(self, note_id: int, soft: bool = True) -> None:
        ...

    def search_notes(self, query: str) -> list[dict[str, Any]]:
        ...


class TaskStorePort(Protocol):
    """任务存储接口 — 对应 PersonalTaskRepository 能力子集。"""

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        ...

    def save_task(self, task_info: dict[str, Any]) -> int:
        """新增或保存任务，返回任务 ID。"""
        ...

    def list_tasks(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        ...

    def complete_task(self, task_id: int) -> None:
        ...


class SettingsPort(Protocol):
    """设置存储接口 — 对应 SettingsRepository。"""

    def get_setting(self, key: str, default: Any = None) -> Any:
        ...

    def set_setting(self, key: str, value: Any) -> None:
        ...

    def get_all_settings(self) -> dict[str, Any]:
        ...


class NewsStorePort(Protocol):
    """新闻记录存储接口 — 对应 PersonalNewsRepository 能力子集。"""

    def get_article(self, article_id: str) -> dict[str, Any] | None:
        ...

    def save_article(self, article: dict[str, Any]) -> None:
        ...

    def list_recent_articles(self, limit: int = 20) -> list[dict[str, Any]]:
        ...
