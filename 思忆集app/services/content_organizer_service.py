"""统一内容整理服务。

将"转笔记、转任务、稍后看、忽略"四个整理动作收口到同一服务，
确保从任何对话框触发都走相同的动作链和状态语义。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domain.inbox_models import (
    STATUS_DISMISSED,
    STATUS_ORGANIZED,
    InboxEntry,
)

logger = logging.getLogger("siyi.content_organizer")


@dataclass
class ContentOrganizeRequest:
    """标准化内容整理请求。

    所有来源（新闻、笔记、翻译、浏览器捕获等）必须使用此结构
    发起整理动作，确保字段一致。
    """

    # ── 内容核心字段 ──
    title: str = ""
    content: str = ""  # 纯文本或 HTML 正文
    source_type: str = "unknown"  # news / note / translation / capture

    # ── 来源标识 ──
    source_id: Any = None  # 来源记录的本地 ID
    source_url: str = ""
    source_name: str = ""

    # ── 分类与标签 ──
    category: str = ""
    tags: list[str] = field(default_factory=list)

    # ── 收件箱关联 ──
    inbox_entry_id: str | None = None

    # ── 来源专有扩展（传递额外的业务字段） ──
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── 是否已收藏（save_for_later 使用） ──
    is_favorite: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": str(self.title or "").strip(),
            "content": str(self.content or "").strip(),
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "source_name": self.source_name,
            "category": self.category,
            "tags": [str(t).strip() for t in (self.tags or []) if str(t).strip()],
            "inbox_entry_id": self.inbox_entry_id,
            "metadata": dict(self.metadata or {}),
            "is_favorite": bool(self.is_favorite),
        }


class ContentOrganizerService:
    """统一内容整理服务。

    提供四类整理动作，确保从任一入口（新闻/笔记/翻译/浏览器）
    触发后，动作链和状态语义保持一致。

    使用方式::

        service = ContentOrganizerService(
            note_repository=app_context.personal_note_repository,
            task_repository=app_context.personal_task_repository,
            news_repository=app_context.personal_news_repository,
            inbox_service=app_context.assistant_inbox_service,
            settings_repository=app_context.settings_repository,
        )
        result = service.convert_to_note(request)
    """

    def __init__(
        self,
        *,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        inbox_service=None,
        settings_repository=None,
    ):
        self._note_repo = note_repository
        self._task_repo = task_repository
        self._news_repo = news_repository
        self._inbox_service = inbox_service
        self._settings_repo = settings_repository

    # ── 转笔记 ──────────────────────────────────────────────

    def convert_to_note(self, request: ContentOrganizeRequest) -> dict[str, Any]:
        """将内容转换为笔记。

        Returns
        -------
        dict
            {"note_id": ..., "title": ..., "success": bool, "error": str}
        """
        if self._note_repo is None:
            return {"note_id": None, "title": request.title, "success": False, "error": "笔记仓储未就绪"}

        resolved_title = request.title or "未命名笔记"
        note_data = {
            "title": resolved_title,
            "content": request.content or "",
            "tags": list(request.tags or []),
            "category": request.category or "通用",
            "folder_path": "/",
        }
        # 携带来源链接
        if request.source_url:
            note_data["related_news_url"] = request.source_url
        if request.source_id is not None:
            note_data["related_news_id"] = request.source_id
        # 携带 metadata 中的扩展字段
        note_data.update({k: v for k, v in request.metadata.items() if k not in note_data})

        try:
            note_id = self._note_repo.add_note(note_data)
        except Exception as exc:
            return {"note_id": None, "title": resolved_title, "success": False, "error": str(exc)}

        # 标记收件箱对应条目的状态
        self._mark_inbox_entry_organized(request)

        return {
            "note_id": note_id,
            "title": resolved_title,
            "success": True,
            "error": "",
        }

    # ── 转任务 ──────────────────────────────────────────────

    def convert_to_task(self, request: ContentOrganizeRequest) -> dict[str, Any]:
        """将内容转换为任务。

        Returns
        -------
        dict
            {"task_id": ..., "name": ..., "success": bool, "error": str}
        """
        if self._task_repo is None:
            return {"task_id": None, "name": request.title, "success": False, "error": "任务仓储未就绪"}

        resolved_title = request.title or "未命名任务"
        # 截取前 200 字符作为任务备注
        note_text = _strip_html(request.content)[:200]

        task_info = {
            "name": f"跟进: {resolved_title}",
            "note": note_text,
            "is_completed": False,
            "completed": False,
            "reminder_time": None,
        }
        if request.source_id is not None:
            task_info["linked_note_id"] = request.source_id
        if request.source_url:
            task_info["link"] = request.source_url

        try:
            task_id = self._task_repo.add_task(task_info)
        except Exception as exc:
            return {"task_id": None, "name": resolved_title, "success": False, "error": str(exc)}

        self._mark_inbox_entry_organized(request)

        return {
            "task_id": task_id,
            "name": resolved_title,
            "success": True,
            "error": "",
        }

    # ── 稍后看（收藏） ───────────────────────────────────────

    def save_for_later(self, request: ContentOrganizeRequest) -> dict[str, Any]:
        """标记内容为"稍后看/收藏"。

        对新闻来源，设置收藏状态并同步到笔记仓库。
        对笔记来源，切换收藏标记。

        Returns
        -------
        dict
            {"favorite": bool, "success": bool, "error": str}
        """
        if request.source_type == "news" and self._news_repo is not None:
            key = request.source_url or request.title
            if not key:
                return {"favorite": request.is_favorite, "success": False, "error": "缺少新闻标识"}
            try:
                self._news_repo.set_news_favorite(
                    str(key).strip(),
                    request.is_favorite,
                    request.metadata.get("news_record"),
                )
            except Exception as exc:
                return {"favorite": request.is_favorite, "success": False, "error": str(exc)}

            if request.is_favorite and self._note_repo is not None:
                self._convert_news_to_note_on_favorite(request)

            return {"favorite": request.is_favorite, "success": True, "error": ""}

        if request.source_type == "note" and self._note_repo is not None:
            if request.source_id is not None:
                try:
                    self._note_repo.update_note(
                        request.source_id,
                        {"favorite": request.is_favorite},
                        keep_version=False,
                    )
                except Exception as exc:
                    return {"favorite": request.is_favorite, "success": False, "error": str(exc)}
            return {"favorite": request.is_favorite, "success": True, "error": ""}

        return {"favorite": request.is_favorite, "success": False, "error": f"不支持的来源类型: {request.source_type}"}

    # ── 忽略 ────────────────────────────────────────────────

    def dismiss(self, request: ContentOrganizeRequest) -> dict[str, Any]:
        """标记内容为忽略，不再提醒或展示。

        Returns
        -------
        dict
            {"success": bool, "error": str}
        """
        if request.inbox_entry_id and self._inbox_service is not None:
            try:
                self._inbox_service.dismiss(request.inbox_entry_id)
            except Exception as exc:
                return {"success": False, "error": str(exc)}
            return {"success": True, "error": ""}

        if request.source_type == "news" and self._news_repo is not None:
            key = request.source_url or request.title
            if key:
                try:
                    self._news_repo.set_news_favorite(str(key).strip(), False, None)
                except Exception:
                    logger.warning("failed to remove news favorite during unfavorite")

        return {"success": True, "error": ""}

    # ── 内部辅助 ──────────────────────────────────────────

    def _mark_inbox_entry_organized(self, request: ContentOrganizeRequest) -> None:
        """将收件箱条目标记为已整理。"""
        if request.inbox_entry_id and self._inbox_service is not None:
            try:
                self._inbox_service.dismiss(request.inbox_entry_id)
            except Exception:
                logger.warning("failed to dismiss inbox entry")

    def _convert_news_to_note_on_favorite(self, request: ContentOrganizeRequest) -> None:
        """收藏新闻时同步创建笔记。"""
        note_request = ContentOrganizeRequest(
            title=f"新闻收藏: {request.title}",
            content=request.content,
            source_type="news",
            source_id=request.source_id,
            source_url=request.source_url,
            tags=["新闻收藏"],
            category="新闻收藏",
            metadata=request.metadata,
        )
        self.convert_to_note(note_request)


def _strip_html(value: str) -> str:
    """简易 HTML 标签去除。"""
    import re
    return re.sub(r"<[^>]+>", "", value or "").strip()
