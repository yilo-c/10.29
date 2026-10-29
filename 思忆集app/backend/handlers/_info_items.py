# -*- coding: utf-8 -*-
"""info.items.* RPC handlers — 信息仓库 CRUD。

这些方法依赖 context.info_item_repository，与 InfoCollectorService 解耦。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, List

logger = logging.getLogger("siyi.backend.handlers.info_items")

_CATEGORY_LABELS = {
    "ai": "AI与大模型",
    "dev": "技术动态",
    "tool": "工具与开源",
    "internet": "互联网与产品",
    "science": "科技突破",
    "policy": "政策与监管",
    "domestic": "国内动态",
    "international": "国际动态",
    "local": "同城",
    "general": "其他资讯",
}


def _extract_interest_topics(item: "dict[str, Any]") -> "List[str]":
    """从收藏的资讯条目提取用户可能感兴趣的主题。"""
    topics: List[str] = []
    cat = item.get("category", "general")
    label = _CATEGORY_LABELS.get(cat, cat)
    if label:
        topics.append(label)
    topics.extend(str(t).strip() for t in item.get("tags", []) if str(t).strip())
    title = item.get("title_zh") or item.get("title") or ""
    for kw in re.findall(r"[A-Za-z][A-Za-z0-9_\-\.]{1,11}", title):
        if kw.lower() not in (t.lower() for t in topics):
            topics.append(kw)
    return list(dict.fromkeys(topics))[:8]


async def register(registry, context: "Any") -> None:
    """注册 info.items.* JSON-RPC 方法。

    闭包捕获 context 与 context.info_item_repository。
    repository 缺失时所有方法安全降级，仅返回错误（不影响后端启动）。
    """
    repo = getattr(context, "info_item_repository", None)
    if repo is None:
        logger.warning("info_item_repository not available, info.items.* methods will fail")

    async def info_items_list(
        source: "str | None" = None,
        category: "str | None" = None,
        saved: "bool | None" = None,
        read: "bool | None" = None,
        search: "str | None" = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "collected_at",
        sort_desc: bool = True,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """列出信息条目，支持多维过滤与排序。

        Returns: {"items": [...], "total": int}
        """
        if repo is None:
            return {"items": [], "total": 0, "error": "repository unavailable"}
        try:
            items, total = await asyncio.to_thread(
                lambda: repo.list_items(
                    search=search or "",
                    limit=min(max(int(limit), 1), 500),
                    offset=max(0, int(offset)),
                    read=read,
                    saved=saved,
                    source=source or None,
                    category=category or None,
                    sort_by=sort_by,
                    sort_desc=sort_desc,
                ),
            )
            return {"items": items, "total": total}
        except Exception as exc:
            logger.warning("info.items.list failed: %s", exc)
            return {"items": [], "total": 0, "error": str(exc)}

    async def info_items_get(item_id: "str | int" = "", **kwargs: Any) -> "dict[str, Any]":
        """获取单条信息条目详情。"""
        if repo is None or not item_id:
            return {"item": None, "error": "invalid item_id"}
        try:
            item = await asyncio.to_thread(repo.get_item, item_id)
            return {"item": item}
        except Exception as exc:
            logger.warning("info.items.get failed: %s", exc)
            return {"item": None, "error": str(exc)}

    async def info_items_toggle_read(
        item_id: "str | int" = "",
        read: "bool | None" = None,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """标记已读/未读。

        显式传入 read 时直接 update_item；否则调用 toggle_read 翻转状态。
        """
        if repo is None or not item_id:
            return {"item": None, "error": "invalid item_id"}
        try:
            if read is not None:
                item = await asyncio.to_thread(repo.update_item, item_id, {"read": bool(read)})
            else:
                item = await asyncio.to_thread(repo.toggle_read, item_id)
            return {"item": item}
        except Exception as exc:
            logger.warning("info.items.toggle_read failed: %s", exc)
            return {"item": None, "error": str(exc)}

    async def info_items_toggle_saved(
        item_id: "str | int" = "",
        saved: "bool | None" = None,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """标记收藏/取消收藏。

        显式传入 saved 时直接 update_item；否则调用 toggle_saved 翻转状态。
        当某条目被标记为已收藏（saved=True）时，尝试从该条目提取兴趣主题，
        合并到用户画像的 focus_topics（失败静默忽略）。
        """
        if repo is None or not item_id:
            return {"item": None, "error": "invalid item_id"}
        try:
            if saved is not None:
                item = await asyncio.to_thread(repo.update_item, item_id, {"saved": bool(saved)})
            else:
                item = await asyncio.to_thread(repo.toggle_saved, item_id)
            if item and item.get("saved"):
                profile_svc = getattr(context, "user_profile_service", None)
                if profile_svc is not None:
                    topics = _extract_interest_topics(item)
                    if topics:
                        try:
                            await asyncio.to_thread(profile_svc.merge_focus_topics, topics)
                        except Exception as exc:
                            logger.debug("profile learn from saved info item failed: %s", exc)
            return {"item": item}
        except Exception as exc:
            logger.warning("info.items.toggle_saved failed: %s", exc)
            return {"item": None, "error": str(exc)}

    async def info_items_update(
        item_id: "str | int" = "",
        notes: "str | None" = None,
        tags: "list[str] | None" = None,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """更新信息条目的备注（notes）或标签（tags）。"""
        if repo is None or not item_id:
            return {"item": None, "error": "invalid item_id"}
        patch: dict[str, Any] = {}
        if notes is not None:
            patch["notes"] = notes
        if tags is not None:
            patch["tags"] = list(tags)
        if not patch:
            return {"item": None, "error": "no fields to update"}
        try:
            item = await asyncio.to_thread(repo.update_item, item_id, patch)
            return {"item": item}
        except Exception as exc:
            logger.warning("info.items.update failed: %s", exc)
            return {"item": None, "error": str(exc)}

    async def info_items_sources(**kwargs: Any) -> "dict[str, Any]":
        """返回所有信息来源（source 维度去重列表）。"""
        if repo is None:
            return {"sources": []}
        try:
            sources = await asyncio.to_thread(repo.get_sources)
            return {"sources": sources}
        except Exception as exc:
            logger.warning("info.items.sources failed: %s", exc)
            return {"sources": [], "error": str(exc)}

    async def info_items_stats(**kwargs: Any) -> "dict[str, Any]":
        """返回信息仓库统计（各状态/分类计数）。"""
        if repo is None:
            return {"stats": {}}
        try:
            stats = await asyncio.to_thread(repo.get_stats)
            return {"stats": stats}
        except Exception as exc:
            logger.warning("info.items.stats failed: %s", exc)
            return {"stats": {}, "error": str(exc)}

    async def info_items_save_to_note(
        item_id: "str | int" = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """将信息条目保存为笔记。"""
        if repo is None or not item_id:
            return {"success": False, "error": "invalid item_id"}
        note_repo = getattr(context, "personal_note_repository", None)
        if note_repo is None:
            return {"success": False, "error": "note repository unavailable"}
        try:
            item = await asyncio.to_thread(repo.get_item, item_id)
            if item is None:
                return {"success": False, "error": "item not found"}
            title = item.get("title_zh") or item.get("title") or "资讯收藏"
            url = item.get("url", "")
            summary = item.get("summary_zh") or item.get("summary") or ""
            source = item.get("source_label") or item.get("source") or ""
            content_parts = [f"原文链接：{url}"]
            if source:
                content_parts.append(f"\n来源：{source}")
            if summary:
                content_parts.append(f"\n摘要：\n{summary}")
            if item.get("notes"):
                content_parts.append(f"\n备注：\n{item['notes']}")
            content = "\n".join(content_parts)
            # 修 keyword/位置误调（2026-09-16 批3普查）：NoteRepository.
            # add_note 真实签名是单参数 dict（repositories/_note.py:85），
            # 此处以 5 个位置参数调用必抛 TypeError 被 except 吞成
            # success=False——「资讯保存为笔记」自该 handler 诞生起静默失效。
            note_id = await asyncio.to_thread(
                note_repo.add_note,
                {
                    "title": title,
                    "content": content,
                    "category": "新闻收藏",
                    "tags": ["资讯"] + (list(item.get("tags") or [])),
                    "favorite": bool(item.get("saved")),
                    "source_url": url,
                },
            )
            return {"success": True, "note_id": note_id}
        except Exception as exc:
            logger.warning("info.items.save_to_note failed: %s", exc)
            return {"success": False, "error": str(exc)}

    # P2 修复（rpc-census-2026-08-30）：get/toggle_read/toggle_saved/update/
    # save_to_note 原签名要求必填位置参数 item_id，registry.dispatch 以
    # handler(**params) 展开调用，空参直接 TypeError（-32603 broken）。
    # 改为：签名给默认值（直调兜底走既有 invalid item_id 降级）+ 注册层
    # JSON Schema required=item_id（空参返回干净 -32602 InvalidParamsError）。
    _required_item_id: dict[str, Any] = {"type": "object", "required": ["item_id"]}
    registry.register("info.items.list", info_items_list)
    registry.register_with_schema("info.items.get", info_items_get, _required_item_id)
    registry.register_with_schema("info.items.toggle_read", info_items_toggle_read, _required_item_id)
    registry.register_with_schema("info.items.toggle_saved", info_items_toggle_saved, _required_item_id)
    registry.register_with_schema("info.items.update", info_items_update, _required_item_id)
    registry.register("info.items.sources", info_items_sources)
    registry.register("info.items.stats", info_items_stats)
    registry.register_with_schema("info.items.save_to_note", info_items_save_to_note, _required_item_id)
