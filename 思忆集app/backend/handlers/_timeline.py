# -*- coding: utf-8 -*-
"""Timeline aggregation JSON-RPC handlers (P2.4).

跨数据源时间线聚合：notes / tasks / inbox / info_items / translations / chat_sessions / vocab_words。
所有方法在仓储不可用时安全降级返回空结果。同步 I/O 通过 ``asyncio.to_thread`` 包裹避免阻塞事件循环。

依赖的 AppContext 属性（均为可选，缺失时该数据源被跳过）：
    - ``personal_note_repository``
    - ``personal_task_repository``
    - ``personal_inbox_repository``
    - ``info_item_repository``
    - ``translation_history_repository``
    - ``chat_session_repository``
    - ``vocabulary_repository``

参考实现：
    - ``memory/index.py:_collect_entries()`` 跨源聚合模式
    - ``backend/handlers/_audit.py`` handler 模板（limit clamp + 异常降级）
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

logger = logging.getLogger("siyi.backend.handlers.timeline")

SOURCE_TYPES = ("note", "task", "inbox", "info", "translation", "chat", "vocab")


def _parse_ts(ts: Any) -> datetime | None:
    """解析时间戳为带 UTC 时区的 datetime。失败返回 None。

    支持：
    - datetime 对象（无时区视为 UTC）
    - UNIX 时间戳（int/float）
    - ISO 8601 字符串（含 ``Z`` 后缀）
    - 常见 ``%Y-%m-%d %H:%M:%S`` / ``%Y-%m-%d`` / ``%Y/%m/%d`` 格式
    """
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if not isinstance(ts, str):
        return None
    s = ts.strip()
    if not s:
        return None
    # ISO 8601 with Z suffix
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    # Common formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _encode_cursor(offset: int) -> str:
    """将 offset 编码为不透明的 cursor 字符串。"""
    return base64.urlsafe_b64encode(json.dumps({"o": max(0, int(offset))}).encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> int:
    """将 cursor 解码为 offset。失败返回 0。"""
    if not cursor:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        return max(0, int(data.get("o", 0)))
    except Exception:
        logger.exception("解码时间线分页游标失败")
        return 0


def _build_entry(
    *,
    source_type: str,
    source_id: Any,
    title: str,
    summary: str,
    updated_at: Any,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造归一化的时间线条目。"""
    return {
        "entry_id": f"{source_type}:{source_id}",
        "source_type": source_type,
        "source_id": source_id,
        "title": str(title or "").strip()[:200],
        "summary": str(summary or "").strip()[:500],
        "updated_at": str(updated_at or "").strip(),
        "tags": list(tags or []),
        "metadata": dict(metadata or {}),
    }


def _count_by_type(entries: Iterable[dict[str, Any]]) -> dict[str, int]:
    """按 source_type 计数。"""
    counts: dict[str, int] = {}
    for e in entries:
        t = str(e.get("source_type", "unknown"))
        counts[t] = counts.get(t, 0) + 1
    return counts


def _collect_notes(repo, limit: int) -> list[dict[str, Any]]:
    if repo is None:
        return []
    try:
        items = repo.list_notes(include_deleted=False)
    except Exception as exc:
        logger.info("timeline skipped notes: %s", exc, exc_info=True)
        return []
    entries = []
    for note in items[: max(0, limit)]:
        ts = note.get("updated_at") or note.get("created_at")
        if not ts:
            continue
        entries.append(
            _build_entry(
                source_type="note",
                source_id=note.get("id"),
                title=note.get("title") or "未命名笔记",
                summary=(note.get("content") or "")[:200],
                updated_at=ts,
                tags=note.get("tags") or [],
                metadata={
                    "folder_path": note.get("folder_path") or "/",
                    "favorite": bool(note.get("favorite")),
                },
            )
        )
    return entries


def _collect_tasks(repo, limit: int) -> list[dict[str, Any]]:
    if repo is None:
        return []
    try:
        items = repo.list_tasks(include_deleted=False)
    except Exception as exc:
        logger.info("timeline skipped tasks: %s", exc, exc_info=True)
        return []
    entries = []
    for task in items[: max(0, limit)]:
        ts = task.get("updated_at") or task.get("created_at")
        if not ts:
            continue
        entries.append(
            _build_entry(
                source_type="task",
                source_id=task.get("id"),
                title=task.get("title") or "未命名任务",
                summary=(task.get("description") or "")[:200],
                updated_at=ts,
                tags=[],
                metadata={
                    "priority": task.get("priority"),
                    "status": task.get("status"),
                    "is_completed": bool(task.get("is_completed") or task.get("completed")),
                    "due_date": task.get("due_date"),
                },
            )
        )
    return entries


def _collect_inbox(repo, limit: int) -> list[dict[str, Any]]:
    if repo is None:
        return []
    try:
        items = repo.list_entries(include_archived=False)
    except Exception as exc:
        logger.info("timeline skipped inbox: %s", exc, exc_info=True)
        return []
    entries = []
    for entry in items[: max(0, limit)]:
        ts = entry.get("updated_at") or entry.get("created_at")
        if not ts:
            continue
        entries.append(
            _build_entry(
                source_type="inbox",
                source_id=entry.get("id"),
                title=entry.get("title") or "未命名收件箱条目",
                summary=(entry.get("content") or "")[:200],
                updated_at=ts,
                tags=entry.get("tags") or [],
                metadata={
                    "source": entry.get("source"),
                    "status": entry.get("status"),
                    "kind": entry.get("kind"),
                    "channel": entry.get("channel"),
                },
            )
        )
    return entries


def _collect_info_items(repo, limit: int) -> list[dict[str, Any]]:
    if repo is None:
        return []
    try:
        # 忠实重建：旧版即按此签名调用（当前 list_items 不接受 sort_by/
        # sort_desc，由下方 except 兜底为跳过该源），勿改调用形式。
        result = repo.list_items(limit=max(0, limit), sort_by="collected_at", sort_desc=True)
        items = result[0] if isinstance(result, tuple) else result
    except Exception as exc:
        logger.info("timeline skipped info_items: %s", exc, exc_info=True)
        return []
    entries = []
    for item in items:
        ts = item.get("collected_at") or item.get("updated_at") or item.get("created_at")
        if not ts:
            continue
        entries.append(
            _build_entry(
                source_type="info",
                source_id=item.get("id"),
                title=item.get("title_zh") or item.get("title") or "未命名资讯",
                summary=(item.get("summary_zh") or item.get("summary") or "")[:200],
                updated_at=ts,
                tags=item.get("tags") or [],
                metadata={
                    "source": item.get("source"),
                    "source_label": item.get("source_label"),
                    "category": item.get("category"),
                    "url": item.get("url"),
                    "saved": bool(item.get("saved")),
                    "read": bool(item.get("read")),
                    "score": item.get("score"),
                },
            )
        )
    return entries


def _collect_translations(repo, limit: int) -> list[dict[str, Any]]:
    if repo is None:
        return []
    try:
        items = repo.list_translation_records(include_deleted=False)
    except Exception as exc:
        logger.info("timeline skipped translations: %s", exc, exc_info=True)
        return []
    entries = []
    for rec in items[: max(0, limit)]:
        ts = rec.get("updated_at") or rec.get("created_at")
        if not ts:
            continue
        entries.append(
            _build_entry(
                source_type="translation",
                source_id=rec.get("id"),
                title=(rec.get("source_text") or "")[:80] or "未命名翻译",
                summary=(rec.get("translated_text") or "")[:200],
                updated_at=ts,
                tags=[],
                metadata={
                    "from_lang": rec.get("from_lang"),
                    "to_lang": rec.get("to_lang"),
                },
            )
        )
    return entries


def _collect_chat_sessions(repo, limit: int) -> list[dict[str, Any]]:
    if repo is None:
        return []
    try:
        items = repo.list_sessions()
    except Exception as exc:
        logger.info("timeline skipped chat_sessions: %s", exc, exc_info=True)
        return []
    entries = []
    for sess in items[: max(0, limit)]:
        ts = sess.get("updated_at") or sess.get("created_at")
        if not ts:
            continue
        entries.append(
            _build_entry(
                source_type="chat",
                source_id=sess.get("id"),
                title=sess.get("title") or "未命名对话",
                summary="",
                updated_at=ts,
                tags=[],
                metadata={"message_count": sess.get("message_count", 0)},
            )
        )
    return entries


def _collect_vocab_words(repo, limit: int) -> list[dict[str, Any]]:
    if repo is None:
        return []
    try:
        items = repo.list_words(include_deleted=False)
    except Exception as exc:
        logger.info("timeline skipped vocab_words: %s", exc, exc_info=True)
        return []
    entries = []
    for word in items[: max(0, limit)]:
        ts = word.get("updated_at") or word.get("created_at")
        if not ts:
            continue
        entries.append(
            _build_entry(
                source_type="vocab",
                source_id=word.get("id"),
                title=word.get("word") or "未命名单词",
                summary=(word.get("meaning") or "")[:200],
                updated_at=ts,
                tags=[],
                metadata={
                    "wordbook_id": word.get("wordbook_id"),
                    "wordbook_name": word.get("wordbook_name"),
                },
            )
        )
    return entries


def _aggregate(
    context,
    *,
    source_types: Iterable[str] | None = None,
    per_source_limit: int = 500,
) -> list[dict[str, Any]]:
    """跨数据源聚合时间线条目。

    Args:
        source_types: 指定要采集的数据源类型；为 None 或空时采集全部。
        per_source_limit: 每个数据源最多采集的条目数。

    Returns:
        按 ``updated_at`` 倒序排列的归一化条目列表。
    """
    selected = set(source_types) if source_types else set(SOURCE_TYPES)
    selected &= set(SOURCE_TYPES)
    if not selected:
        return []

    # 固定采集器表（顺序即 SOURCE_TYPES 声明顺序）。
    # N-F（2026-09-17）：历史实现对 7 个源串行采集，总耗时 ≈ 各源 I/O 之和
    # （timeline.stats 1.3s / list 0.6s，per_source_limit 高达 5000）。改为线程池
    # 并行后总耗时 ≈ 单个最慢源。存储层 SqliteKVStore 用 threading.local 线程本地
    # 连接（每线程独立 _connect），并行读安全。
    collectors: tuple[tuple[str, Callable[[Any, int], list[dict[str, Any]]], str], ...] = (
        ("note", _collect_notes, "personal_note_repository"),
        ("task", _collect_tasks, "personal_task_repository"),
        ("inbox", _collect_inbox, "personal_inbox_repository"),
        ("info", _collect_info_items, "info_item_repository"),
        ("translation", _collect_translations, "translation_history_repository"),
        ("chat", _collect_chat_sessions, "chat_session_repository"),
        ("vocab", _collect_vocab_words, "vocabulary_repository"),
    )
    jobs = [(key, fn, repo_attr) for (key, fn, repo_attr) in collectors if key in selected]

    all_entries: list[dict[str, Any]] = []
    if len(jobs) <= 1:
        # 单源无需线程池开销。
        for key, fn, repo_attr in jobs:
            all_entries.extend(fn(getattr(context, repo_attr, None), per_source_limit))
    else:
        # 并行采集，但按固定 jobs 顺序回填结果（非完成顺序）——保证输出与串行版
        # 字节级一致：下游 sort 稳定，equal updated_at 的平局顺序仍取决于此处插入序。
        ex = ThreadPoolExecutor(max_workers=min(7, len(jobs)), thread_name_prefix="timeline")
        try:
            futures = [ex.submit(fn, getattr(context, repo_attr, None), per_source_limit) for (_key, fn, repo_attr) in jobs]
            for fut in futures:
                # 与串行版语义一致：collector 内部已 try/except 兜底返回 []，
                # 此处若仍抛出（极端异常）则向上传播，由 handler 统一降级为
                # error 响应——串行版同样是异常穿透 _aggregate 后丢弃已采集结果。
                all_entries.extend(fut.result())
        finally:
            ex.shutdown(wait=False)

    def _sort_key(e: dict[str, Any]) -> tuple[int, datetime]:
        dt = _parse_ts(e.get("updated_at"))
        if dt is None:
            return (0, datetime.min.replace(tzinfo=timezone.utc))
        return (1, dt)

    all_entries.sort(key=_sort_key, reverse=True)
    return all_entries


def _filter_by_range(entries: list[dict[str, Any]], start_at: str, end_at: str) -> list[dict[str, Any]]:
    """按时间范围 ``[start_at, end_at]`` 过滤条目（闭区间）。"""
    start_dt = _parse_ts(start_at)
    end_dt = _parse_ts(end_at)
    if start_dt is None and end_dt is None:
        return entries
    result = []
    for e in entries:
        dt = _parse_ts(e.get("updated_at"))
        if dt is None:
            continue
        if start_dt is not None and dt < start_dt:
            continue
        if end_dt is not None and dt > end_dt:
            continue
        result.append(e)
    return result


def register_timeline(registry, context) -> None:
    """Register timeline-related JSON-RPC methods (P2.4)."""

    async def timeline_list(
        limit: int = 50,
        source_types: list[str] | None = None,
        cursor: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        clamped_limit = min(max(int(limit), 1), 500)
        offset = _decode_cursor(cursor)
        # 预留缓冲，避免边界分页丢条目
        per_source_limit = offset + clamped_limit + 50
        try:
            all_entries = await asyncio.to_thread(_aggregate, context, source_types=source_types, per_source_limit=per_source_limit)
        except Exception as exc:
            logger.warning("timeline.list failed: %s", exc)
            return {"error": str(exc), "entries": [], "next_cursor": "", "counts_by_type": {}}

        page = all_entries[offset : offset + clamped_limit]
        has_more = len(all_entries) > offset + clamped_limit
        next_cursor = _encode_cursor(offset + clamped_limit) if has_more else ""

        return {
            "entries": page,
            "count": len(page),
            "next_cursor": next_cursor,
            "counts_by_type": _count_by_type(all_entries),
            "total": len(all_entries),
            "offset": offset,
        }

    registry.register("timeline.list", timeline_list)

    async def timeline_range(
        start_at: str = "",
        end_at: str = "",
        source_types: list[str] | None = None,
        limit: int = 100,
        **kwargs: Any,
    ) -> dict[str, Any]:
        clamped_limit = min(max(int(limit), 1), 1000)
        try:
            all_entries = await asyncio.to_thread(_aggregate, context, source_types=source_types, per_source_limit=2000)
        except Exception as exc:
            logger.warning("timeline.range failed: %s", exc)
            return {"error": str(exc), "entries": [], "counts_by_type": {}}

        filtered = _filter_by_range(all_entries, start_at, end_at)
        page = filtered[:clamped_limit]
        return {
            "entries": page,
            "count": len(page),
            "total_in_range": len(filtered),
            "counts_by_type": _count_by_type(page),
            "start_at": start_at,
            "end_at": end_at,
        }

    registry.register("timeline.range", timeline_range)

    async def timeline_by_type(
        source_type: str = "",
        limit: int = 50,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not source_type:
            return {
                "error": "source_type is required",
                "entries": [],
                "valid_types": list(SOURCE_TYPES),
            }

        if source_type not in SOURCE_TYPES:
            return {
                "error": f"invalid source_type: {source_type}",
                "entries": [],
                "valid_types": list(SOURCE_TYPES),
            }

        clamped_limit = min(max(int(limit), 1), 500)
        try:
            all_entries = await asyncio.to_thread(_aggregate, context, source_types=[source_type], per_source_limit=clamped_limit)
        except Exception as exc:
            logger.warning("timeline.by_type failed: %s", exc)
            return {"error": str(exc), "entries": []}

        return {
            "source_type": source_type,
            "entries": all_entries[:clamped_limit],
            "count": len(all_entries),
        }

    registry.register("timeline.by_type", timeline_by_type)

    async def timeline_stats(
        days: int = 30,
        source_types: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        clamped_days = min(max(int(days), 1), 365)
        try:
            all_entries = await asyncio.to_thread(_aggregate, context, source_types=source_types, per_source_limit=5000)
        except Exception as exc:
            logger.warning("timeline.stats failed: %s", exc)
            return {"error": str(exc)}

        now = datetime.now(timezone.utc)
        cutoff_ts = now.timestamp() - clamped_days * 86400
        by_type: dict[str, int] = {}
        by_day: dict[str, int] = {}
        by_hour: dict[int, int] = {}
        recent_by_type: dict[str, int] = {}
        recent_total = 0
        for e in all_entries:
            t = str(e.get("source_type", "unknown"))
            by_type[t] = by_type.get(t, 0) + 1
            dt = _parse_ts(e.get("updated_at"))
            if dt is None:
                continue
            # 按本地时区统计日/小时分布
            local_dt = dt.astimezone()
            day_str = local_dt.strftime("%Y-%m-%d")
            by_day[day_str] = by_day.get(day_str, 0) + 1
            hour_key = local_dt.hour
            by_hour[hour_key] = by_hour.get(hour_key, 0) + 1

            if dt.timestamp() >= cutoff_ts:
                recent_total += 1
                recent_by_type[t] = recent_by_type.get(t, 0) + 1

        return {
            "total": len(all_entries),
            "recent_days": clamped_days,
            "recent_total": recent_total,
            "by_type": by_type,
            "by_day": by_day,
            "by_hour": {str(k): v for k, v in sorted(by_hour.items())},
            "recent_by_type": recent_by_type,
            "source_types": list(SOURCE_TYPES),
        }

    registry.register("timeline.stats", timeline_stats)
    logger.info("timeline handlers registered (P2.4): timeline.list/range/by_type/stats")
