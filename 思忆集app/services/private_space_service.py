"""私人空间三层落盘状态机。

PrivateSpaceService 将收件后的条目推进 inbox → daily → insights 三层模型，
确保 version、device_id、sync_state 在状态转换中保持不变。

三层定义：
- inbox:      新捕获的原始条目（待处理）
- daily:      活跃/进行中的条目（正在处理）
- insights:   归档/沉淀后的条目（已整理的知识）

支持的资源类型：notes, personal_tasks, saved_articles
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("siyi.private_space")

# ── 三层状态 ─────────────────────────────────────────────
SPACE_INBOX = "inbox"
SPACE_DAILY = "daily"
SPACE_INSIGHTS = "insights"

ALL_SPACES = frozenset({SPACE_INBOX, SPACE_DAILY, SPACE_INSIGHTS})

# ── 支持的资源类型 ────────────────────────────────────────
RESOURCE_NOTES = "notes"
RESOURCE_TASKS = "personal_tasks"
RESOURCE_SAVED_ARTICLES = "saved_articles"

ALL_RESOURCE_TYPES = frozenset({
    RESOURCE_NOTES, RESOURCE_TASKS, RESOURCE_SAVED_ARTICLES,
})

# 资源类型 → data_store JSON key 映射
_DATA_STORE_KEY_MAP: dict[str, str] = {
    RESOURCE_NOTES: "notes",
    RESOURCE_TASKS: "tasks",
    RESOURCE_SAVED_ARTICLES: "news",
}

# 允许的 transition 方向（可选约束，当前支持任意方向）
_VALID_TRANSITIONS = {
    SPACE_INBOX: {SPACE_DAILY, SPACE_INSIGHTS, SPACE_INBOX},
    SPACE_DAILY: {SPACE_INBOX, SPACE_INSIGHTS, SPACE_DAILY},
    SPACE_INSIGHTS: {SPACE_INBOX, SPACE_DAILY, SPACE_INSIGHTS},
}


class PrivateSpaceService:
    """私人空间三层状态机。

    管理 notes / personal_tasks / saved_articles 三种资源
    在 inbox → daily → insights 之间的状态转换，
    并确保同步元数据（version、device_id、sync_state）不受影响。
    """

    def __init__(self, *, data_store, note_repository=None, task_repository=None, news_repository=None):
        self.data_store = data_store
        self.note_repository = note_repository
        self.task_repository = task_repository
        self.news_repository = news_repository

    # ── 公开 API ───────────────────────────────────────────

    def set_space(
        self,
        resource_type: str,
        item_id: int,
        to_space: str,
    ) -> dict[str, Any] | None:
        """将指定条目转换到目标空间。

        转换过程中 version、device_id、sync_state 保持原值不变。
        返回更新后的条目 dict，若条目不存在则返回 None。

        Args:
            resource_type: 资源类型（notes / personal_tasks / saved_articles）
            item_id: 条目 ID
            to_space: 目标空间（inbox / daily / insights）

        Returns:
            更新后的条目 dict，或 None（未找到或参数无效）
        """
        resource_type = str(resource_type or "").strip()
        to_space = str(to_space or "").strip()

        if not self._validate_params(resource_type, to_space):
            return None

        store_key = _DATA_STORE_KEY_MAP[resource_type]

        def updater(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal found_item
            result = []
            for item in items or []:
                if isinstance(item, dict) and item.get("id") == item_id:
                    item = self._apply_space_transition(item, to_space)
                    found_item = item
                result.append(item)
            return result

        found_item: dict[str, Any] | None = None
        self.data_store.update_json(store_key, [], updater)

        if found_item is not None:
            logger.debug(
                "space transition: %s[%s] -> %s (preserved: v=%s, device=%s, sync=%s)",
                resource_type, item_id, to_space,
                found_item.get("version"), found_item.get("device_id"), found_item.get("sync_state"),
            )

        return found_item

    def get_space(self, resource_type: str, item_id: int) -> str | None:
        """获取指定条目当前所在空间。条目不存在时返回 None。"""
        store_key = _DATA_STORE_KEY_MAP.get(str(resource_type or "").strip())
        if store_key is None:
            return None

        items = self.data_store.load_json(store_key, [])
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get("id") == item_id:
                space = str(item.get("space") or SPACE_INBOX).strip()
                return space if space in ALL_SPACES else SPACE_INBOX
        return None

    def list_by_space(
        self,
        resource_type: str,
        space: str,
    ) -> list[dict[str, Any]]:
        """列出指定空间中的所有条目。

        Args:
            resource_type: 资源类型
            space: 目标空间

        Returns:
            匹配的条目列表（不包含 deleted 条目）
        """
        resource_type = str(resource_type or "").strip()
        space = str(space or "").strip()

        store_key = _DATA_STORE_KEY_MAP.get(resource_type)
        if store_key is None or space not in ALL_SPACES:
            return []

        items = self.data_store.load_json(store_key, [])
        if not isinstance(items, list):
            return []

        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("deleted_at"):
                continue
            item_space = str(item.get("space") or SPACE_INBOX).strip()
            if item_space == space or (not item.get("space") and space == SPACE_INBOX):
                result.append(dict(item))
        return result

    def count_by_space(self, resource_type: str) -> dict[str, int]:
        """统计指定资源类型在各空间的条目数。"""
        store_key = _DATA_STORE_KEY_MAP.get(str(resource_type or "").strip())
        if store_key is None:
            return {SPACE_INBOX: 0, SPACE_DAILY: 0, SPACE_INSIGHTS: 0}

        counts = {SPACE_INBOX: 0, SPACE_DAILY: 0, SPACE_INSIGHTS: 0}
        items = self.data_store.load_json(store_key, [])
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or item.get("deleted_at"):
                continue
            space = str(item.get("space") or SPACE_INBOX).strip()
            if space in counts:
                counts[space] += 1
            else:
                counts[SPACE_INBOX] += 1
        return counts

    def list_all_spaces(self, resource_type: str) -> dict[str, list[dict[str, Any]]]:
        """以空间为分组的全量条目列表。"""
        result: dict[str, list[dict[str, Any]]] = {
            SPACE_INBOX: [],
            SPACE_DAILY: [],
            SPACE_INSIGHTS: [],
        }
        store_key = _DATA_STORE_KEY_MAP.get(str(resource_type or "").strip())
        if store_key is None:
            return result

        items = self.data_store.load_json(store_key, [])
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or item.get("deleted_at"):
                continue
            space = str(item.get("space") or SPACE_INBOX).strip()
            if space in result:
                result[space].append(dict(item))
            else:
                result[SPACE_INBOX].append(dict(item))
        return result

    # ── 内部 ───────────────────────────────────────────────

    @staticmethod
    def _validate_params(resource_type: str, space: str) -> bool:
        """验证资源类型和空间参数是否合法。"""
        if resource_type not in ALL_RESOURCE_TYPES:
            logger.debug("invalid resource_type: %s (expected one of %s)", resource_type, ALL_RESOURCE_TYPES)
            return False
        if space not in ALL_SPACES:
            logger.debug("invalid space: %s (expected one of %s)", space, ALL_SPACES)
            return False
        return True

    @staticmethod
    def _preserve_sync_fields(item: dict[str, Any]) -> dict[str, Any]:
        """从条目中提取必须保持不变的同步元数据字段。"""
        return {
            "version": item.get("version"),
            "device_id": item.get("device_id"),
            "sync_state": item.get("sync_state"),
        }

    @staticmethod
    def _apply_space_transition(item: dict[str, Any], to_space: str) -> dict[str, Any]:
        """对条目应用空间转换，保留同步元数据。"""
        old_space = str(item.get("space") or SPACE_INBOX).strip()

        sync_fields = PrivateSpaceService._preserve_sync_fields(item)

        item["space"] = to_space

        for key, value in sync_fields.items():
            if value is not None:
                item[key] = value

        logger.debug(
            "  transition detail: %s -> %s (id=%s, v=%s)",
            old_space, to_space, item.get("id"), item.get("version"),
        )

        return item
