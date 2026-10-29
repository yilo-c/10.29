"""Weekly and daily report generation scheduler.

Collects weekly stats (AI usage, notes/tasks changes, sync status),
generates a natural-language summary via AI, and posts it to inbox.

F-3: Also supports a daily summary mode with notes, tasks, chat highlights,
vocabulary progress, and key inbox items.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from core.clock import wall_now, wall_ts
from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.backend.weekly_report")


def _utc_now() -> datetime:
    """UTC 墙钟读数——本模块唯一允许的"读现在"入口。

    经 ``core.clock.wall_ts()`` 派生（时间纪律棘轮：本模块不得直读
    ``datetime.now()``）；语义与旧 ``datetime.now(timezone.utc)`` 逐字等价，
    迁移不改行为。
    """
    return datetime.fromtimestamp(wall_ts(), tz=timezone.utc)


_scheduler_task: asyncio.Task | None = None
_enabled = False
_context_ref: object | None = None
_last_run_date: str | None = None  # YYYY-MM-DD of last successful run

_PERSIST_KEY = "weekly_report_last_run_date"

# F-3: daily summary scheduler state
# P2-8（晨报卡）：默认启用并提前至 07:00 —— 产品边界文档"必须做"清单的
# 每日聚合入口（昨日任务/到期单词/收件箱/统计）；经 configure_daily 可关闭/改时。
_daily_scheduler_task: asyncio.Task | None = None
_daily_enabled = True
_daily_summary_time = "07:00"
_daily_last_run_date: str | None = None
_DAILY_PERSIST_KEY = "daily_summary_last_run_date"


def _persist_last_run_date(date_str: str) -> None:
    """Best-effort persist last-run date through context.local_data_store."""
    ctx = _context_ref
    if ctx is None:
        return
    try:
        ds = getattr(ctx, "local_data_store", None)
        if ds is not None and hasattr(ds, "save_json"):
            ds.save_json(_PERSIST_KEY, {"date": date_str})
    except Exception as exc:
        logger.debug("weekly report persist failed: %s", exc)


def _load_last_run_date() -> str | None:
    """Best-effort load last-run date from context.local_data_store."""
    ctx = _context_ref
    if ctx is None:
        return None
    try:
        ds = getattr(ctx, "local_data_store", None)
        if ds is not None and hasattr(ds, "load_json"):
            data = ds.load_json(_PERSIST_KEY, {})
            if isinstance(data, dict):
                value = data.get("date")
                if isinstance(value, str) and value:
                    return value
    except Exception as exc:
        logger.debug("weekly report load failed: %s", exc)
    return None


def _persist_daily_last_run_date(date_str: str) -> None:
    """Best-effort persist daily last-run date through context.local_data_store."""
    ctx = _context_ref
    if ctx is None:
        return
    try:
        ds = getattr(ctx, "local_data_store", None)
        if ds is not None and hasattr(ds, "save_json"):
            ds.save_json(_DAILY_PERSIST_KEY, {"date": date_str})
    except Exception as exc:
        logger.debug("daily summary persist failed: %s", exc)


def _load_daily_last_run_date() -> str | None:
    """Best-effort load daily last-run date from context.local_data_store."""
    ctx = _context_ref
    if ctx is None:
        return None
    try:
        ds = getattr(ctx, "local_data_store", None)
        if ds is not None and hasattr(ds, "load_json"):
            data = ds.load_json(_DAILY_PERSIST_KEY, {})
            if isinstance(data, dict):
                value = data.get("date")
                if isinstance(value, str) and value:
                    return value
    except Exception as exc:
        logger.debug("daily summary load failed: %s", exc)
    return None


def get_config() -> dict:
    return {
        "enabled": _enabled,
        "last_run_date": _last_run_date,
        "daily_enabled": _daily_enabled,
        "daily_summary_time": _daily_summary_time,
        "daily_last_run_date": _daily_last_run_date,
    }


async def _collect_weekly_stats(context) -> dict:
    """Collect statistics for the past week from all available sources."""
    stats: dict = {}

    # AI cost stats
    try:
        ct = getattr(context, "cost_tracker", None)
        if ct is not None:
            stats["ai"] = ct.get_status_snapshot()
    except Exception as exc:
        logger.debug("failed to collect AI stats: %s", exc)

    # Notes stats
    try:
        note_repo = getattr(context, "personal_note_repository", None)
        if note_repo is not None:
            notes = note_repo.list_notes()
            week_ago = wall_ts() * 1000 - 7 * 86400 * 1000
            recent = [n for n in notes if n.get("created_at") or n.get("updated_at")]
            stats["notes"] = {
                "total": len(notes),
                "recent_count": sum(1 for n in recent if _parse_ts(n.get("created_at", "")) > week_ago),
            }
    except Exception as exc:
        logger.debug("failed to collect notes stats: %s", exc)

    # Tasks stats
    try:
        task_repo = getattr(context, "personal_task_repository", None)
        if task_repo is not None:
            tasks = task_repo.list_tasks()
            week_ago = wall_ts() * 1000 - 7 * 86400 * 1000
            done = [t for t in tasks if t.get("done")]
            stats["tasks"] = {
                "total": len(tasks),
                "done": len(done),
                "done_this_week": sum(1 for t in done if _parse_ts(t.get("updated_at", "")) > week_ago),
                "pending": len(tasks) - len(done),
            }
    except Exception as exc:
        logger.debug("failed to collect tasks stats: %s", exc)

    # P3-6: Inbox stats — 区分本周新增 / 当前待处理 / 已归档，按 kind 分桶
    try:
        inbox_repo = getattr(context, "personal_inbox_repository", None)
        if inbox_repo is not None:
            all_entries = inbox_repo.list_entries(include_archived=True)
            week_ago = wall_ts() * 1000 - 7 * 86400 * 1000
            new_this_week = [e for e in all_entries if _parse_ts(e.get("created_at", "")) > week_ago]
            pending = [e for e in all_entries if str(e.get("status") or "").lower() not in ("archived", "done", "dismissed")]
            by_kind: dict[str, int] = {}
            for e in new_this_week:
                k = str(e.get("kind") or e.get("metadata", {}).get("ai_classify", {}).get("kind") or "other").lower()
                by_kind[k] = by_kind.get(k, 0) + 1
            stats["inbox"] = {
                "total": len(all_entries),
                "new_this_week": len(new_this_week),
                "pending": len(pending),
                "by_kind": by_kind,
            }
    except Exception as exc:
        logger.debug("failed to collect inbox stats: %s", exc)

    return stats


def _parse_ts(ts_str: str) -> float:
    """Parse ISO timestamp string to milliseconds-since-epoch, or 0 on failure."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp() * 1000
    except (ValueError, TypeError):
        return 0


def _local_today_str() -> str:
    """Return today's date as YYYY-MM-DD in local time."""
    return wall_now().strftime("%Y-%m-%d")


def _local_day_bounds(date_str: str) -> tuple[datetime, datetime]:
    """Return naive start/end datetimes for a local YYYY-MM-DD date."""
    start = datetime.strptime(date_str, "%Y-%m-%d")
    end = start + timedelta(days=1, microseconds=-1)
    return start, end


def _parse_ts_to_local(ts_str: str) -> datetime | None:
    """Parse an ISO timestamp and return a naive local datetime."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        # Convert UTC-aware to local naive
        if dt.tzinfo is not None:
            import time

            local_ts = dt.timestamp() + time.localtime().tm_gmtoff
            return datetime.fromtimestamp(local_ts)
        return dt
    except (ValueError, TypeError):
        return None


def _is_same_local_day(ts_str: str, date_str: str) -> bool:
    """Check whether an ISO timestamp falls on the given local date."""
    dt = _parse_ts_to_local(ts_str)
    if dt is None:
        return False
    start, end = _local_day_bounds(date_str)
    return start <= dt <= end


async def _collect_daily_stats(context, target_date: str | None = None) -> dict:
    """Collect statistics for a single day (default: today, local time)."""
    stats: dict = {}
    date_str = target_date or _local_today_str()

    # Notes created today
    try:
        note_repo = getattr(context, "personal_note_repository", None)
        if note_repo is not None:
            notes = note_repo.list_notes()
            created_today = [n for n in notes if _is_same_local_day(n.get("created_at", ""), date_str)]
            stats["notes"] = {
                "total": len(notes),
                "created_today": len(created_today),
                "today_titles": [str(n.get("title") or "未命名")[:40] for n in created_today[:5]],
            }
    except Exception as exc:
        logger.debug("failed to collect daily notes stats: %s", exc)

    # Tasks completed today / scheduled today / pending
    try:
        task_repo = getattr(context, "personal_task_repository", None)
        if task_repo is not None:
            tasks = task_repo.list_tasks()
            done_today = [t for t in tasks if t.get("done") and _is_same_local_day(t.get("updated_at", ""), date_str)]
            scheduled_today = [t for t in tasks if not t.get("done") and _is_due_on_local_day(t.get("due_date", ""), date_str)]
            pending = [t for t in tasks if not t.get("done")]
            stats["tasks"] = {
                "total": len(tasks),
                "done_today": len(done_today),
                "scheduled_today": len(scheduled_today),
                "pending": len(pending),
            }
    except Exception as exc:
        logger.debug("failed to collect daily tasks stats: %s", exc)

    # AI chat highlights: messages today and a short extract
    try:
        chat_repo = getattr(context, "chat_session_repository", None)
        if chat_repo is not None:
            sessions = chat_repo.list_sessions()
            total_user_today = 0
            total_assistant_today = 0
            latest_snippets: list[str] = []
            for session in sessions:
                sid = session.get("id")
                if sid is None:
                    continue
                messages = chat_repo.get_recent_messages(sid, count=50)
                for msg in messages:
                    ts = msg.get("timestamp") or msg.get("created_at") or ""
                    if not _is_same_local_day(ts, date_str):
                        continue
                    speaker = str(msg.get("speaker") or "").lower()
                    text = str(msg.get("message") or "").strip()
                    if speaker in ("user", "你", "human"):
                        total_user_today += 1
                    elif speaker in ("assistant", "ai", "助手"):
                        total_assistant_today += 1
                    if text and len(latest_snippets) < 3:
                        latest_snippets.append(text[:80])
            stats["chat"] = {
                "sessions": len(sessions),
                "user_messages_today": total_user_today,
                "assistant_messages_today": total_assistant_today,
                "latest_snippets": latest_snippets,
            }
    except Exception as exc:
        logger.debug("failed to collect daily chat stats: %s", exc)

    # Vocabulary / learning progress
    try:
        vocab_repo = getattr(context, "vocabulary_repository", None)
        if vocab_repo is not None:
            words = vocab_repo.list_words()
            wordbooks = vocab_repo.list_wordbooks()
            added_today = [w for w in words if _is_same_local_day(w.get("created_at", ""), date_str)]
            due_today = getattr(vocab_repo, "count_due", lambda _wb: 0)(1)
            stats["vocab"] = {
                "total_words": len(words),
                "wordbooks": len(wordbooks),
                "added_today": len(added_today),
                "due_today": due_today,
            }
    except Exception as exc:
        logger.debug("failed to collect daily vocab stats: %s", exc)

    # Key inbox items added today
    try:
        inbox_repo = getattr(context, "personal_inbox_repository", None)
        if inbox_repo is not None:
            all_entries = inbox_repo.list_entries(include_archived=True)
            added_today = [e for e in all_entries if _is_same_local_day(e.get("created_at", ""), date_str)]
            pending = [e for e in all_entries if str(e.get("status") or "").lower() not in ("archived", "done", "dismissed")]
            by_kind: dict[str, int] = {}
            for e in added_today:
                k = str(e.get("kind") or e.get("metadata", {}).get("ai_classify", {}).get("kind") or "other").lower()
                by_kind[k] = by_kind.get(k, 0) + 1
            stats["inbox"] = {
                "total": len(all_entries),
                "added_today": len(added_today),
                "pending": len(pending),
                "by_kind": by_kind,
                "today_titles": [str(e.get("title") or "未命名")[:40] for e in added_today[:5]],
            }
    except Exception as exc:
        logger.debug("failed to collect daily inbox stats: %s", exc)

    return stats


def _is_due_on_local_day(due_str: str, date_str: str) -> bool:
    """Check whether a due_date ISO string falls on the given local date."""
    if not due_str:
        return False
    try:
        dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            import time

            local_ts = dt.timestamp() + time.localtime().tm_gmtoff
            dt = datetime.fromtimestamp(local_ts)
        start, end = _local_day_bounds(date_str)
        return start <= dt <= end
    except (ValueError, TypeError):
        return False


async def _generate_report_text(context, stats: dict) -> str:
    """Use AI to generate a natural-language weekly summary from collected stats."""
    ai_client = getattr(context, "ai_client", None)
    if ai_client is None:
        return _format_fallback_report(stats)

    lines = ["你是一个个人助手，根据以下本周统计数据生成一段自然语言周报。"]
    lines.append("语气亲切但简洁，2-4句话即可，列出关键数字。无需标题。")

    ai_stats = stats.get("ai", {})
    if ai_stats:
        lines.append(
            f"AI调用：本周共{ai_stats.get('total_calls', 0)}次，"
            f"今日费用${ai_stats.get('today_cost', 0):.4f}，"
            f"本月费用${ai_stats.get('month_cost', 0):.4f}"
        )

    notes_stats = stats.get("notes", {})
    if notes_stats:
        lines.append(f"笔记：共{notes_stats.get('total', 0)}条，本周新增{notes_stats.get('recent_count', 0)}条")

    tasks_stats = stats.get("tasks", {})
    if tasks_stats:
        lines.append(
            f"任务：共{tasks_stats.get('total', 0)}项，已完成{tasks_stats.get('done', 0)}项（本周完成{tasks_stats.get('done_this_week', 0)}项），待办{tasks_stats.get('pending', 0)}项"
        )

    inbox_stats = stats.get("inbox", {})
    if inbox_stats:
        by_kind = inbox_stats.get("by_kind") or {}
        kind_brief = "、".join(f"{k}{v}" for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])[:4]) if by_kind else ""
        line = f"收件箱：共{inbox_stats.get('total', 0)}条，本周新增{inbox_stats.get('new_this_week', 0)}条，待处理{inbox_stats.get('pending', 0)}条"
        if kind_brief:
            line += f"（{kind_brief}）"
        lines.append(line)

    lines.append("请用中文回复。")

    prompt = "\n".join(lines)

    try:
        # chat_completion 是同步方法，必须用 to_thread 包装避免 await dict 抛 TypeError
        result = await asyncio.to_thread(
            ai_client.chat_completion,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        text = ""
        if isinstance(result, dict):
            text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        elif isinstance(result, str):
            text = result
        if text:
            now_str = _utc_now().strftime("%Y-%m-%d")
            return f"📊 本周小结 ({now_str})\n\n{text.strip()}"
    except Exception as exc:
        logger.warning("AI weekly report generation failed: %s", exc)

    return _format_fallback_report(stats)


def _format_fallback_report(stats: dict) -> str:
    """Generate a plain-text report without AI."""
    now_str = _utc_now().strftime("%Y-%m-%d")
    parts = [f"📊 本周小结 ({now_str})"]

    ai_stats = stats.get("ai", {})
    if ai_stats:
        parts.append(f"· AI调用{ai_stats.get('total_calls', 0)}次，本月费用${ai_stats.get('month_cost', 0):.4f}")

    notes_stats = stats.get("notes", {})
    if notes_stats:
        parts.append(f"· 共{notes_stats.get('total', 0)}条笔记，本周+{notes_stats.get('recent_count', 0)}")

    tasks_stats = stats.get("tasks", {})
    if tasks_stats:
        parts.append(f"· 任务{tasks_stats.get('total', 0)}项，已完成{tasks_stats.get('done', 0)}项，待办{tasks_stats.get('pending', 0)}项")

    inbox_stats = stats.get("inbox", {})
    if inbox_stats:
        by_kind = inbox_stats.get("by_kind") or {}
        kind_brief = "、".join(f"{k}{v}" for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])[:4]) if by_kind else ""
        line = f"· 收件箱{inbox_stats.get('total', 0)}条，本周+{inbox_stats.get('new_this_week', 0)}，待处理{inbox_stats.get('pending', 0)}"
        if kind_brief:
            line += f"（{kind_brief}）"
        parts.append(line)

    return "\n".join(parts)


async def _post_to_inbox(context, text: str) -> None:
    """Post the generated report to the user's inbox."""
    repo = getattr(context, "personal_inbox_repository", None)
    if repo is None:
        logger.warning("inbox repository not available, skipping post")
        return
    try:
        repo.add_entry(
            {
                "title": "📊 周报",
                "content": text,
                "kind": "report",
                "tags": ["周报", "自动生成"],
                "status": "new",
            }
        )
        logger.info("weekly report posted to inbox")
    except Exception as exc:
        logger.error("failed to post weekly report to inbox: %s", exc)


async def _generate_daily_summary_text(context, stats: dict, target_date: str | None = None) -> str:
    """Use AI to generate a natural-language daily summary from collected stats."""
    ai_client = getattr(context, "ai_client", None)
    if ai_client is None:
        return _format_fallback_daily_report(stats, target_date)

    date_str = target_date or _local_today_str()
    lines = [f"你是一个个人助手，根据以下 {date_str} 的统计数据生成一段自然语言日报。"]
    lines.append("语气亲切但简洁，2-4句话即可，列出关键数字和一件值得回顾的亮点。无需标题。")

    notes_stats = stats.get("notes", {})
    if notes_stats:
        lines.append(f"笔记：今日新建{notes_stats.get('created_today', 0)}条（共{notes_stats.get('total', 0)}条）")

    tasks_stats = stats.get("tasks", {})
    if tasks_stats:
        lines.append(
            f"任务：今日完成{tasks_stats.get('done_today', 0)}项，"
            f"今日计划{tasks_stats.get('scheduled_today', 0)}项，"
            f"待办{tasks_stats.get('pending', 0)}项"
        )

    chat_stats = stats.get("chat", {})
    if chat_stats:
        lines.append(f"AI对话：今日你发送{chat_stats.get('user_messages_today', 0)}条，助手回复{chat_stats.get('assistant_messages_today', 0)}条")
        snippets = chat_stats.get("latest_snippets") or []
        if snippets:
            lines.append(f"最新对话片段：{' / '.join(snippets[:2])}")

    vocab_stats = stats.get("vocab", {})
    if vocab_stats:
        lines.append(
            f"词库：共{vocab_stats.get('total_words', 0)}个单词，"
            f"今日新增{vocab_stats.get('added_today', 0)}个，"
            f"今日到期{vocab_stats.get('due_today', 0)}个"
        )

    inbox_stats = stats.get("inbox", {})
    if inbox_stats:
        by_kind = inbox_stats.get("by_kind") or {}
        kind_brief = "、".join(f"{k}{v}" for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])[:3]) if by_kind else ""
        line = f"收件箱：今日新增{inbox_stats.get('added_today', 0)}条，待处理{inbox_stats.get('pending', 0)}条"
        if kind_brief:
            line += f"（{kind_brief}）"
        lines.append(line)

    lines.append("请用中文回复。")

    prompt = "\n".join(lines)

    try:
        result = await asyncio.to_thread(
            ai_client.chat_completion,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        text = ""
        if isinstance(result, dict):
            text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        elif isinstance(result, str):
            text = result
        if text:
            return f"📋 日报 ({date_str})\n\n{text.strip()}"
    except Exception as exc:
        logger.warning("AI daily summary generation failed: %s", exc)

    return _format_fallback_daily_report(stats, target_date)


def _format_fallback_daily_report(stats: dict, target_date: str | None = None) -> str:
    """Generate a plain-text daily report without AI."""
    date_str = target_date or _local_today_str()
    parts = [f"📋 日报 ({date_str})"]

    notes_stats = stats.get("notes", {})
    if notes_stats:
        parts.append(f"· 笔记：今日+{notes_stats.get('created_today', 0)}，共{notes_stats.get('total', 0)}")

    tasks_stats = stats.get("tasks", {})
    if tasks_stats:
        parts.append(
            f"· 任务：今日完成{tasks_stats.get('done_today', 0)}，"
            f"今日计划{tasks_stats.get('scheduled_today', 0)}，"
            f"待办{tasks_stats.get('pending', 0)}"
        )

    chat_stats = stats.get("chat", {})
    if chat_stats:
        parts.append(f"· AI对话：今日{chat_stats.get('user_messages_today', 0)}条提问，{chat_stats.get('assistant_messages_today', 0)}条回复")

    vocab_stats = stats.get("vocab", {})
    if vocab_stats:
        parts.append(
            f"· 词库：共{vocab_stats.get('total_words', 0)}词，今日+{vocab_stats.get('added_today', 0)}，到期{vocab_stats.get('due_today', 0)}"
        )

    inbox_stats = stats.get("inbox", {})
    if inbox_stats:
        by_kind = inbox_stats.get("by_kind") or {}
        kind_brief = "、".join(f"{k}{v}" for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])[:3]) if by_kind else ""
        line = f"· 收件箱：今日+{inbox_stats.get('added_today', 0)}，待处理{inbox_stats.get('pending', 0)}"
        if kind_brief:
            line += f"（{kind_brief}）"
        parts.append(line)

    return "\n".join(parts)


async def _post_daily_to_inbox(context, text: str) -> int:
    """Post the generated daily summary to the user's inbox. Returns entry id."""
    repo = getattr(context, "personal_inbox_repository", None)
    if repo is None:
        logger.warning("inbox repository not available, skipping daily summary post")
        return 0
    try:
        entry_id = repo.add_entry(
            {
                "title": "📋 日报",
                "content": text,
                "kind": "daily_report",
                "tags": ["日报", "自动生成"],
                "status": "new",
            }
        )
        logger.info("daily summary posted to inbox (id=%s)", entry_id)
        return entry_id
    except Exception as exc:
        logger.error("failed to post daily summary to inbox: %s", exc)
        return 0


def _emit_daily_summary_ready(text: str, inbox_id: int, stats: dict) -> None:
    """Emit an event so the frontend can show a notification."""
    try:
        event_bus.emit(
            AppEvent(
                "daily_summary.ready",
                {
                    "date": _local_today_str(),
                    "inbox_id": inbox_id,
                    "summary": text[:300],
                    "stats": {
                        "notes_created": stats.get("notes", {}).get("created_today", 0),
                        "tasks_done": stats.get("tasks", {}).get("done_today", 0),
                        "tasks_pending": stats.get("tasks", {}).get("pending", 0),
                        "chat_messages": stats.get("chat", {}).get("assistant_messages_today", 0),
                        "vocab_added": stats.get("vocab", {}).get("added_today", 0),
                        "inbox_added": stats.get("inbox", {}).get("added_today", 0),
                    },
                },
            )
        )
    except Exception as exc:
        logger.debug("daily_summary.ready event emit failed: %s", exc)
    # P2-8：同步走 proactive.triggered 通道（trigger=morning_brief），
    # 复用主动建议气泡 + 通知白名单链路，前端/手机端无需新事件类型。
    try:
        event_bus.emit(
            AppEvent(
                "proactive.triggered",
                {
                    "trigger": "morning_brief",
                    "title": "今日晨报",
                    "content": text[:200],
                    "priority": "normal",
                    "source": "daily_summary",
                    "source_id": str(inbox_id or ""),
                    "channel": "inbox",
                    "tags": ["proactive", "morning_brief"],
                    "metadata": {"date": _local_today_str(), "inbox_id": inbox_id},
                },
            )
        )
    except Exception as exc:
        logger.debug("morning_brief proactive event emit failed: %s", exc)


async def _run_weekly() -> None:
    """One-shot: collect stats, generate report, post to inbox."""
    global _last_run_date
    context = _context_ref
    if context is None:
        logger.warning("context not set, skipping weekly report")
        return

    logger.info("generating weekly report...")
    try:
        stats = await _collect_weekly_stats(context)
        text = await _generate_report_text(context, stats)
        await _post_to_inbox(context, text)
        _last_run_date = _utc_now().strftime("%Y-%m-%d")
        _persist_last_run_date(_last_run_date)
        logger.info("weekly report generated and posted")
    except Exception:
        logger.error("weekly report generation failed", exc_info=True)


async def _loop() -> None:
    """Background loop: check every hour if it's Monday and run.

    P3-9: 之前仅在 ``now.hour == 1`` 这 1 个小时窗口内触发，若刚好进程在
    01:00–02:00 重启就会错过整周。改为：在任意 Monday 调度日且
    ``_last_run_date`` 与今日 UTC 日期不同时即可补跑，保证至少每周一次。
    """
    global _enabled
    while _enabled:
        try:
            await asyncio.sleep(3600)  # check every hour
            if not _enabled:
                break
            now = _utc_now()
            # 任意 Monday（≈ 09:00 CST 周一）都允许补跑当周报告
            if now.weekday() == 0:
                today_str = now.strftime("%Y-%m-%d")
                if _last_run_date != today_str:
                    await _run_weekly()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("weekly report loop error: %s", exc)


def _parse_hhmm(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' into (hour, minute); default to 09:00 on failure."""
    try:
        parts = str(time_str or "09:00").split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, IndexError):
        pass
    return 9, 0


def _should_run_daily() -> bool:
    """Check whether the daily summary should run now (local time)."""
    today_str = wall_now().strftime("%Y-%m-%d")
    if _daily_last_run_date is None:
        return True
    if _daily_last_run_date == today_str:
        return False
    now = wall_now()
    hour, minute = _parse_hhmm(_daily_summary_time)
    if now.hour < hour:
        return False
    if now.hour == hour and now.minute < minute:
        return False
    return True


async def _run_daily(target_date: str | None = None, context=None) -> dict:
    """One-shot: collect daily stats, generate summary, post to inbox, emit event.

    S3-2（2026-09-16）：``context`` 可选覆盖 —— cron worker 侧执行时把它的
    AppContext 传进来（旧路径继续用模块级 ``_context_ref``）。
    """
    global _daily_last_run_date
    if context is None:
        context = _context_ref
    if context is None:
        logger.warning("context not set, skipping daily summary")
        return {"success": False, "error": "context not set"}

    inbox_repo = getattr(context, "personal_inbox_repository", None)
    if inbox_repo is None:
        logger.warning("inbox repository not available, cannot generate daily summary")
        return {"success": False, "error": "inbox repository not available"}

    logger.info("generating daily summary...")
    try:
        stats = await _collect_daily_stats(context, target_date)
        text = await _generate_daily_summary_text(context, stats, target_date)
        inbox_id = await _post_daily_to_inbox(context, text)
        _emit_daily_summary_ready(text, inbox_id, stats)
        _daily_last_run_date = _local_today_str()
        _persist_daily_last_run_date(_daily_last_run_date)
        logger.info("daily summary generated and posted")
        return {"success": True, "report_length": len(text), "inbox_id": inbox_id, "stats": stats}
    except Exception as exc:
        logger.error("daily summary generation failed", exc_info=True)
        return {"success": False, "error": str(exc)}


async def _daily_loop() -> None:
    """Background loop: check every hour if it's time for the daily summary."""
    global _daily_enabled
    while _daily_enabled:
        try:
            await asyncio.sleep(3600)  # check every hour
            if not _daily_enabled:
                break
            if _should_run_daily():
                await _run_daily()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("daily summary loop error: %s", exc)


def set_context(context) -> None:
    """Set the app context reference (injected at startup)."""
    global _context_ref, _last_run_date, _daily_last_run_date
    _context_ref = context
    # P3-9: 从持久化加载上次运行日期，避免重启后丢失导致重复发周报
    if _last_run_date is None:
        loaded = _load_last_run_date()
        if loaded:
            _last_run_date = loaded
    # F-3: 同样加载日报上次运行日期
    if _daily_last_run_date is None:
        loaded = _load_daily_last_run_date()
        if loaded:
            _daily_last_run_date = loaded


def restart() -> None:
    """Start or restart the scheduler based on current enabled state."""
    global _scheduler_task
    cancel()
    if _enabled:
        _scheduler_task = asyncio.create_task(_loop())


def restart_daily() -> None:
    """Start or restart the daily summary scheduler based on current enabled state."""
    global _daily_scheduler_task
    cancel_daily()
    if not _daily_enabled:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop yet (e.g. during synchronous configuration in tests)
        return
    _daily_scheduler_task = asyncio.create_task(_daily_loop())


def cancel() -> None:
    """Cancel the running scheduler task."""
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        _scheduler_task = None


def cancel_daily() -> None:
    """Cancel the running daily summary scheduler task."""
    global _daily_scheduler_task
    if _daily_scheduler_task is not None:
        _daily_scheduler_task.cancel()
        _daily_scheduler_task = None


def configure(enabled: bool = True) -> dict:
    """Enable or disable the weekly report scheduler. Returns current config."""
    global _enabled
    _enabled = enabled
    restart()
    logger.info("weekly report scheduler %s", "enabled" if enabled else "disabled")
    return {"success": True, "enabled": _enabled}


def configure_daily(enabled: bool = False, time: str = "09:00") -> dict:
    """Enable or disable the daily summary scheduler. Returns current config."""
    global _daily_enabled, _daily_summary_time
    _daily_enabled = enabled
    _daily_summary_time = time or "07:00"
    restart_daily()
    logger.info("daily summary scheduler %s (time=%s)", "enabled" if enabled else "disabled", _daily_summary_time)
    return {
        "success": True,
        "enabled": _daily_enabled,
        "time": _daily_summary_time,
    }


async def run_daily_summary(target_date: str | None = None, context=None) -> dict:
    """Public entry point: manually trigger a daily summary.

    S3-2：cron worker 经队列执行时传入它的 AppContext（``context=``），
    使手动触发与调度器自跑走同一实现、同一 context。
    """
    return await _run_daily(target_date, context=context)
