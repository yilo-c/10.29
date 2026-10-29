"""Reading position JSON-RPC handlers — cross-device reading continuity.

Supports mobile → desktop reading position sync:
    - reading.push(note_id, scroll_position, percentage, device_id)
    - reading.get(note_id)
    - reading.list()
    - reading.delete(note_id)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

from backend.handlers._list_guard import capped_payload

logger = logging.getLogger("siyi.backend.handlers.reading")

# P1-fix: 保存后台 Task 强引用，防止被 GC 静默回收
_pending_bg_tasks: set[asyncio.Task] = set()


def _get_store_path(context) -> str:
    """Get the reading positions JSON file path."""
    data_store = getattr(context, "local_data_store", None)
    if data_store and hasattr(data_store, "base_dir"):
        return os.path.join(data_store.base_dir, "reading_positions.json")
    # Fallback: use data directory relative to this file
    fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
    return os.path.join(os.path.abspath(fallback), "reading_positions.json")


def _load_positions(path: str) -> dict[str, Any]:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.debug("Failed to load reading positions: %s", exc)
    return {}


def _save_positions(path: str, data: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 原子写：先写临时文件再 os.replace，防止崩溃损坏阅读进度
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("Failed to save reading positions: %s", exc)


def register_reading(registry, context) -> None:
    """Register reading position JSON-RPC methods."""

    # 懒创建 asyncio.Lock：注册期可能尚未进入事件循环，且 Lock 在 3.10+ 必须
    # 在 running loop 内使用。把首次访问推迟到协程内部，确保所有 handler 共享同一把锁。
    io_lock_holder: dict[str, asyncio.Lock] = {}

    def _io_lock() -> asyncio.Lock:
        lock = io_lock_holder.get("lock")
        if lock is None:
            lock = asyncio.Lock()
            io_lock_holder["lock"] = lock
        return lock

    def _get_all() -> dict[str, Any]:
        path = _get_store_path(context)
        return _load_positions(path)

    def _write_all(data: dict[str, Any]) -> None:
        path = _get_store_path(context)
        _save_positions(path, data)

    async def _get_all_async() -> dict[str, Any]:
        async with _io_lock():
            return await asyncio.to_thread(_get_all)

    async def _write_all_async(data: dict[str, Any]) -> None:
        async with _io_lock():
            await asyncio.to_thread(_write_all, data)

    # ── reading.push ──
    async def reading_push(
        note_id: str = "",
        scroll_position: int = 0,
        percentage: float = 0.0,
        device_id: str = "mobile",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Report reading position from a device.

        Args:
            note_id: The note being read.
            scroll_position: Current scroll offset in pixels.
            percentage: Scroll progress as 0-100 float.
            device_id: Source device identifier.
        """
        if not note_id:
            return {"error": "note_id required"}

        now = time.time()
        all_positions = await _get_all_async()

        all_positions[note_id] = {
            "note_id": note_id,
            "scroll_position": max(0, int(scroll_position)),
            "percentage": min(100.0, max(0.0, float(percentage))),
            "device_id": str(device_id),
            "updated_at": now,
        }

        await _write_all_async(all_positions)
        return {"success": True, "note_id": note_id}

    registry.register("reading.push", reading_push)

    # ── reading.get ──
    async def reading_get(note_id: str = "", **kwargs: Any) -> dict[str, Any] | None:
        """Get reading position for a specific note."""
        if not note_id:
            return None
        all_positions = await _get_all_async()
        return all_positions.get(note_id)

    registry.register("reading.get", reading_get)

    # ── reading.list ──
    async def reading_list(**kwargs: Any) -> dict[str, Any]:
        """Get all reading positions, sorted by most recently updated."""
        all_positions = await _get_all_async()
        items = list(all_positions.values())
        items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        # 帧守卫（2026-10-15）：count 保持全量语义，positions 截断 + truncated 标记
        positions, truncated = capped_payload(items, method="reading.list")
        return {"positions": positions, "count": len(items), "truncated": truncated}

    registry.register("reading.list", reading_list)

    # ── reading.delete ──
    async def reading_delete(note_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """Delete a reading position entry."""
        if not note_id:
            return {"error": "note_id required"}
        all_positions = await _get_all_async()
        if note_id in all_positions:
            del all_positions[note_id]
            await _write_all_async(all_positions)
            return {"success": True}
        return {"success": False, "error": "not found"}

    registry.register("reading.delete", reading_delete)

    # ── reading.save_article (稍后读) ──
    def _get_bookmark_path() -> str:
        data_store = getattr(context, "local_data_store", None)
        if data_store and hasattr(data_store, "base_dir"):
            return os.path.join(data_store.base_dir, "saved_articles.json")
        fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
        return os.path.join(os.path.abspath(fallback), "saved_articles.json")

    def _load_bookmarks() -> dict[str, Any]:
        try:
            path = _get_bookmark_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as exc:
            logger.warning("Failed to load bookmarks, starting fresh: %s", exc)
        return {"articles": {}, "order": []}

    def _save_bookmarks(data: dict[str, Any]) -> None:
        try:
            path = _get_bookmark_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Failed to save bookmarks: %s", exc)

    async def _load_bookmarks_async() -> dict[str, Any]:
        async with _io_lock():
            return await asyncio.to_thread(_load_bookmarks)

    async def _save_bookmarks_async(data: dict[str, Any]) -> None:
        async with _io_lock():
            await asyncio.to_thread(_save_bookmarks, data)

    async def _auto_summarize(
        ai_client: Any,
        data_store: Any,
        article_id: str,
        title: str,
        url: str,
        existing_summary: str,
    ) -> None:
        """Generate AI summary for a saved article (background, non-critical)."""
        try:
            text_to_summarize = existing_summary or title
            prompt = f"用一句话概括以下内容（50字以内）：\n\n{text_to_summarize[:2000]}"
            resp = await asyncio.to_thread(
                ai_client.chat_completion,
                messages=[
                    {"role": "system", "content": "你是摘要助手，只返回摘要原文。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=100,
            )
            content = ""
            if isinstance(resp, dict):
                content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            else:
                content = str(resp)
            if content and content.strip():
                # 重新加载最新数据，避免覆盖期间其他更新
                bookmarks = await _load_bookmarks_async()
                if article_id in bookmarks.get("articles", {}):
                    bookmarks["articles"][article_id]["ai_summary"] = content.strip()
                    await _save_bookmarks_async(bookmarks)
        except Exception as exc:
            logger.debug("auto_summarize failed for %s: %s", title[:50], exc)

    async def reading_save_article(
        title: str = "",
        url: str = "",
        source: str = "",
        summary: str = "",
        summary_zh: str = "",
        tags: Any = None,
        title_zh: str = "",
        category: str = "",
        published_at: float = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Save an article for later reading."""
        if not title or not url:
            return {"error": "title and url required"}

        bookmarks = await _load_bookmarks_async()
        article_id = uuid.uuid4().hex[:16]

        bookmarks["articles"][article_id] = {
            "id": article_id,
            "title": title,
            "url": url,
            "source": source,
            "summary": summary,
            "summary_zh": summary_zh,
            "title_zh": title_zh,
            "category": category,
            "tags": list(tags) if isinstance(tags, (list, tuple)) else ([str(tags)] if isinstance(tags, str) and tags else []),
            "published_at": published_at,
            "saved_at": time.time(),
        }
        bookmarks["order"].insert(0, article_id)
        while len(bookmarks["order"]) > 500:
            oldest = bookmarks["order"].pop()
            bookmarks["articles"].pop(oldest, None)

        await _save_bookmarks_async(bookmarks)

        # Auto-summarize in background (non-blocking)
        ai_client = getattr(context, "ai_client", None)
        if ai_client and title and len(title) > 10:
            task = asyncio.ensure_future(_auto_summarize(ai_client, getattr(context, "local_data_store", None), article_id, title, url, summary))
            _pending_bg_tasks.add(task)
            task.add_done_callback(_pending_bg_tasks.discard)

        # 异步：从文章里提议生词卡片，用户在前端勾选入库（不阻塞响应）
        try:
            propose = getattr(context, "_propose_words_for_article", None)
            if callable(propose) and (title or summary):
                task2 = asyncio.ensure_future(propose(article_id, title, summary or ""))
                _pending_bg_tasks.add(task2)
                task2.add_done_callback(_pending_bg_tasks.discard)
        except Exception:
            logger.debug("propose words skipped", exc_info=True)

        return {"success": True, "id": article_id}

    registry.register("reading.save_article", reading_save_article)

    # ── reading.list_articles ──
    async def reading_list_articles(**kwargs: Any) -> dict[str, Any]:
        """List all saved articles, most recently saved first."""
        bookmarks = await _load_bookmarks_async()
        articles = []
        for aid in bookmarks.get("order", []):
            a = bookmarks["articles"].get(aid)
            if a:
                articles.append(a)
        return {"articles": articles, "count": len(articles)}

    registry.register("reading.list_articles", reading_list_articles)

    # ── reading.delete_article ──
    async def reading_delete_article(article_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """Remove a saved article."""
        if not article_id:
            return {"error": "article_id required"}
        bookmarks = await _load_bookmarks_async()
        if article_id in bookmarks["articles"]:
            del bookmarks["articles"][article_id]
            if article_id in bookmarks["order"]:
                bookmarks["order"].remove(article_id)
            await _save_bookmarks_async(bookmarks)
            # PD-3（2026-09-17）：删文章须级联清理图谱实体与边——旧病只删
            # saved_articles，external_content_* 实体+sourced_from 边滞留 →
            # knowledge.search 幽灵命中已删文章。_ensure_knowledge_entity 已在
            # properties.source_id 存裸 article_id（可被 remove_by_source 反查）；
            # 另按确定性 id 直删以兼容本修复前入库的旧实体。
            graph_cleaned = 0
            kg = getattr(context, "knowledge_graph_store", None)
            if kg is not None:
                try:

                    def _kg_cascade():
                        n = kg.remove_by_source("reading_article", article_id)
                        if kg.remove_entity(f"external_content_{article_id}"):
                            n += 1
                        if n:
                            kg.save()
                        return n

                    graph_cleaned = await asyncio.to_thread(_kg_cascade)
                except Exception as exc:
                    # best-effort：文章删除是既成事实，图谱清理失败如实报告不反悔
                    logger.warning("PD-3 graph cascade failed for %s: %s", article_id, exc)
            return {"success": True, "graph_entities_removed": graph_cleaned}
        return {"success": False, "error": "not found"}

    registry.register("reading.delete_article", reading_delete_article)
