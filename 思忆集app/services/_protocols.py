"""服务层接口定义。

定义 services/ 层对外暴露的契约接口（Protocol），
上层（ui/app_context）应依赖这些接口而非具体实现类。
"""

from __future__ import annotations

from typing import Any, Protocol


class WebSearchPort(Protocol):
    """Web 搜索服务接口 — 对应 WebSearchService / WebFetchService。"""

    def search(self, query: str, num_results: int = 5) -> list[Any]:
        ...

    def fetch_page(self, url: str, timeout: int = 10) -> str:
        ...


class ReminderPort(Protocol):
    """提醒服务接口 — 对应 ReminderService。"""

    def get_reminders(self) -> list[dict[str, Any]]:
        ...

    def add_reminder(self, reminder_info: dict[str, Any]) -> None:
        ...

    def dismiss_reminder(self, reminder_id: str | int) -> None:
        ...


class NotificationPort(Protocol):
    """通知推送接口。"""

    def notify(self, title: str, message: str, **kwargs: Any) -> None:
        ...

    def dismiss(self, notification_id: str) -> None:
        ...
