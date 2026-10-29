"""思忆集数据门面（MCP Server 专用）——独立进程安全地读写个人数据。

只依赖 data/ + repositories/（功能路径的数据层），不 import assistant/
与 backend/，保证 MCP Server 可以脱离后端独立运行（内核侧进程）。

通知写入与 ``NotificationDispatcher``（P2.9）共用 ``notifications`` 键
和记录形状，桌面端通知中心可直接看到 agent 推送。
"""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timezone
from typing import Any

from data.local_storage import LocalDataStore
from repositories._inbox import LocalPersonalInboxRepository
from repositories._info_item import LocalInfoItemRepository
from repositories._note import LocalPersonalNoteRepository
from repositories._task import LocalPersonalTaskRepository
from repositories._vocab import LocalVocabularyRepository

logger = logging.getLogger("siyi.mcp_server.store")

# 收件箱启发式分类关键词（无 AI 兜底：MCP 隔离进程不持有 AI 客户端）
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "task": ["待办", "todo", "任务", "做完", "记得", "提醒", "截止", "安排", "计划做"],
    "question": ["?", "？", "吗", "怎么", "为什么", "如何", "什么", "何时", "哪里"],
    "event": ["会议", "约会", "生日", "活动", "聚会", "日程", "面试"],
    "reading": ["文章", "链接", "http", "阅读", "新闻", "news", "read", "订阅"],
    "idea": ["想法", "灵感", "点子", "主意", "创意", "突然想到"],
}

# knowledge_query 意图分类（对齐 knowledge.unified_service 的轻量版）
_INTENT_KEYWORDS: dict[str, list[str]] = {
    "factual": ["是什么", "什么时候", "哪里", "谁", "怎么", "为什么", "原因", "什么时间"],
    "learning": ["学习", "了解", "学一下", "怎么用", "教程", "用法", "如何"],
    "task": ["待办", "进度", "完成", "做了", "任务", "还没", "还有"],
}

# 与 services/notification_dispatcher.py 保持一致（P2.9 约定）
KEY_NOTIFICATIONS = "notifications"
_MAX_NOTIFICATIONS = 500
_PREVIEW_LEN = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview(text: Any, limit: int = _PREVIEW_LEN) -> str:
    s = str(text or "")
    return s if len(s) <= limit else s[:limit] + "…"


class SiyiStore:
    """思忆集个人数据的 MCP 侧门面。"""

    def __init__(self, data_dir: str | None = None) -> None:
        # base_dir=None → 默认生产数据目录（思忆集app/data/data）
        self.store = LocalDataStore(base_dir=data_dir)
        self.notes = LocalPersonalNoteRepository(self.store)
        self.tasks = LocalPersonalTaskRepository(self.store)
        self.inbox = LocalPersonalInboxRepository(self.store)
        self.info = LocalInfoItemRepository(self.store)
        self.vocab = LocalVocabularyRepository(self.store)
        self._lock = threading.Lock()

    @property
    def data_dir(self) -> str:
        return self.store.base_dir

    # ── 健康/统计 ──────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        try:
            note_count = len(self.notes.list_notes())
        except Exception:
            note_count = -1
        try:
            task_count = len(self.tasks.list_tasks())
        except Exception:
            task_count = -1
        try:
            inbox_count = len(self.inbox.list_entries())
        except Exception:
            inbox_count = -1
        try:
            _, news_total = self.info.list_items(limit=0)
        except Exception:
            news_total = -1
        return {
            "server": "siyi-mcp",
            "data_dir": self.data_dir,
            "counts": {"notes": note_count, "tasks": task_count, "inbox": inbox_count, "news": news_total},
            "time_utc": _now_iso(),
        }

    # ── 笔记 ──────────────────────────────────────────────────

    def notes_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        found = self.notes.search_notes(query, max_results=max(1, min(int(limit or 10), 50)))
        return [self._trim_note(n) for n in found]

    def notes_create(self, title: str, content: str, folder_path: str = "/", tags: list[str] | None = None) -> dict[str, Any]:
        now = _now_iso()
        note_info = {
            "title": str(title or "").strip() or "未命名笔记",
            "content": str(content or ""),
            "folder_path": folder_path or "/",
            "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
            "created_at": now,
            "updated_at": now,
        }
        new_id = self.notes.add_note(note_info)
        return {"id": new_id, "title": note_info["title"]}

    def notes_get(self, note_id: str | int) -> dict[str, Any]:
        note = self.notes.get_note(note_id)
        if note is None:
            return {"error": "not_found", "id": note_id}
        return self._trim_note(note)

    def notes_update(
        self,
        note_id: str | int,
        title: str | None = None,
        content: str | None = None,
        folder_path: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        new_info: dict[str, Any] = {}
        if title is not None:
            new_info["title"] = str(title)
        if content is not None:
            new_info["content"] = str(content)
        if folder_path is not None:
            new_info["folder_path"] = folder_path
        if tags is not None:
            new_info["tags"] = [str(t).strip() for t in tags if str(t).strip()]
        if new_info:
            self.notes.update_note(note_id, new_info)
        return {"id": note_id, "updated": bool(new_info)}

    def notes_tag(self, note_id: str | int, tags: list[str]) -> dict[str, Any]:
        clean = [str(t).strip() for t in (tags or []) if str(t).strip()]
        self.notes.update_note(note_id, {"tags": clean})
        return {"id": note_id, "tags": clean}

    @staticmethod
    def _trim_note(n: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": n.get("id"),
            "title": n.get("title", ""),
            "content_preview": _preview(n.get("content")),
            "folder_path": n.get("folder_path", "/"),
            "tags": n.get("tags", []),
            "updated_at": n.get("updated_at", ""),
        }

    # ── 任务 ──────────────────────────────────────────────────

    def tasks_list(self, limit: int = 20, include_completed: bool = False) -> list[dict[str, Any]]:
        tasks = self.tasks.list_tasks()
        if not include_completed:
            tasks = [t for t in tasks if not t.get("is_completed") and not t.get("completed")]
        return [
            {
                "id": t.get("id"),
                "title": t.get("title", ""),
                "priority": t.get("priority", ""),
                "due_date": t.get("due_date"),
                "completed": bool(t.get("is_completed") or t.get("completed")),
                "updated_at": t.get("updated_at", ""),
            }
            for t in tasks[: max(1, min(int(limit or 20), 100))]
        ]

    def tasks_create(self, title: str, priority: str = "medium", due_date: str | None = None) -> dict[str, Any]:
        task_info: dict[str, Any] = {"title": str(title or "").strip() or "未命名任务", "priority": priority}
        if due_date:
            task_info["due_date"] = due_date
        new_id = self.tasks.add_task(task_info)
        return {"id": new_id, "title": task_info["title"]}

    def tasks_toggle(self, task_id: str | int, is_completed: bool = True) -> dict[str, Any]:
        self.tasks.toggle_task_completion(task_id, bool(is_completed))
        return {"id": task_id, "completed": bool(is_completed)}

    def tasks_update(
        self,
        task_id: str | int,
        title: str | None = None,
        priority: str | None = None,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        new_info: dict[str, Any] = {}
        if title is not None:
            new_info["title"] = str(title)
        if priority is not None:
            new_info["priority"] = priority
        if due_date is not None:
            new_info["due_date"] = due_date
        if new_info:
            self.tasks.update_task(task_id, new_info)
        return {"id": task_id, "updated": bool(new_info)}

    # ── 收件箱 ────────────────────────────────────────────────

    def inbox_list(self, limit: int = 20) -> list[dict[str, Any]]:
        entries = self.inbox.list_entries()
        return [
            {
                "id": e.get("id"),
                "title": e.get("title", ""),
                "content_preview": _preview(e.get("content")),
                "status": e.get("status", ""),
                "tags": e.get("tags", []),
                "created_at": e.get("created_at", ""),
            }
            for e in entries[: max(1, min(int(limit or 20), 100))]
        ]

    def inbox_add(self, content: str, title: str = "", tags: list[str] | None = None, source: str = "mcp") -> dict[str, Any]:
        new_id = self.inbox.add_entry({
            "title": str(title or "").strip(),
            "content": str(content or ""),
            "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
            "source": source or "mcp",
        })
        return {"id": new_id}

    def inbox_update(
        self,
        entry_id: str | int,
        title: str | None = None,
        content: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        new_info: dict[str, Any] = {}
        if title is not None:
            new_info["title"] = str(title)
        if content is not None:
            new_info["content"] = str(content)
        if status is not None:
            new_info["status"] = str(status)
        if tags is not None:
            new_info["tags"] = [str(t).strip() for t in tags if str(t).strip()]
        if new_info:
            self.inbox.update_entry(entry_id, new_info)
        return {"id": entry_id, "updated": bool(new_info)}

    # ── 词汇本（FSRS）：复习状态 / 测验打分 ──────────────────────

    def vocab_review_status(self, limit: int = 20) -> dict[str, Any]:
        """返回到期待复习词汇概览（RO）。"""
        now = datetime.now(timezone.utc)
        words = self.vocab.list_words()
        due: list[dict[str, Any]] = []
        for w in words:
            due_iso = w.get("fsrs_due") or w.get("next_review") or ""
            last_review = w.get("fsrs_last_review") or w.get("last_review") or ""
            is_new = (not last_review) and int(w.get("repetitions") or 0) == 0
            is_due = False
            if is_new:
                is_due = True
            elif due_iso:
                try:
                    due_dt = datetime.fromisoformat(due_iso)
                    if due_dt.tzinfo is None:
                        due_dt = due_dt.replace(tzinfo=timezone.utc)
                    is_due = due_dt <= now
                except Exception:
                    is_due = True
            if not is_due:
                continue
            due.append({
                "id": w.get("id"),
                "word": w.get("word", ""),
                "meaning": w.get("meaning", ""),
                "wordbook": w.get("wordbook_name", ""),
                "due": due_iso,
                "is_new": is_new,
            })
        # 新词优先，其余按到期时间升序
        due.sort(key=lambda x: (0 if x["is_new"] else 1, x["due"]))
        return {
            "total_words": len(words),
            "due_count": len(due),
            "due": due[: max(1, min(int(limit or 20), 200))],
        }

    def vocab_quiz(self, word_id, quality) -> dict[str, Any]:
        """记录一次词汇复习打分（RW）。quality: 0-5（FSRS 评级）。"""
        if word_id is None:
            return {"error": "invalid_word_id"}
        try:
            q = int(quality)
        except (TypeError, ValueError):
            return {"error": "invalid_quality", "detail": "quality 必须为整数 0-5"}
        if not (0 <= q <= 5):
            return {"error": "invalid_quality", "detail": "quality 必须为 0-5"}
        result = self.vocab.record_review(int(word_id), q)
        if result is None:
            return {"error": "not_found", "id": word_id}
        reps, efactor, interval, next_review = result
        return {
            "id": word_id,
            "repetitions": reps,
            "efactor": efactor,
            "interval_days": interval,
            "next_review": next_review,
        }

    # ── 信息简报（info_collector 缓存只读视图）──────────────────

    def info_brief(self, limit: int = 15) -> dict[str, Any]:
        """返回信息收集缓存的简报视图（RO）：按来源聚合 + 未读数。"""
        items, total = self.info.list_items(limit=max(1, min(int(limit or 15), 100)))
        unread = sum(1 for it in items if not it.get("read"))
        by_source: dict[str, int] = {}
        for it in items:
            s = it.get("source") or "unknown"
            by_source[s] = by_source.get(s, 0) + 1
        return {
            "total_cached": total,
            "returned": len(items),
            "unread_in_view": unread,
            "by_source": by_source,
            "items": [
                {
                    "id": it.get("id"),
                    "title": it.get("title_zh") or it.get("title", ""),
                    "source": it.get("source", ""),
                    "url": it.get("url", ""),
                    "summary_preview": _preview(it.get("summary_zh") or it.get("summary")),
                    "collected_at": it.get("collected_at") or it.get("created_at", ""),
                    "read": bool(it.get("read")),
                }
                for it in items
            ],
        }

    # ── 跨源检索：knowledge_query / memory_recall ───────────────

    def _cross_search(self, query, per_source: int = 20) -> dict[str, list[dict[str, Any]]]:
        """对 notes/tasks/inbox/info/vocab 做关键词检索，返回按来源分组的命中。"""
        q = (query or "").strip()
        results: dict[str, list[dict[str, Any]]] = {}
        if not q:
            return results
        ql = q.lower()

        notes = []
        for n in self.notes.search_notes(q, max_results=per_source):
            notes.append({
                "id": n.get("id"), "title": n.get("title", ""),
                "content_preview": _preview(n.get("content")),
                "tags": n.get("tags", []), "updated_at": n.get("updated_at", ""),
                "_source": "note",
            })
        results["note"] = notes

        tasks = []
        for t in self.tasks.search_tasks(q, max_results=per_source):
            tasks.append({
                "id": t.get("id"),
                "title": t.get("title") or t.get("name", ""),
                "content_preview": _preview(t.get("note") or t.get("content")),
                "priority": t.get("priority", ""),
                "completed": bool(t.get("is_completed") or t.get("completed")),
                "_source": "task",
            })
        results["task"] = tasks

        inbox = []
        for e in self.inbox.list_entries():
            hay = (str(e.get("title", "")) + " " + str(e.get("content", "")) + " "
                   + " ".join(map(str, e.get("tags", [])))).lower()
            if ql in hay:
                inbox.append({
                    "id": e.get("id"), "title": e.get("title", ""),
                    "content_preview": _preview(e.get("content")),
                    "status": e.get("status", ""), "tags": e.get("tags", []),
                    "_source": "inbox",
                })
        results["inbox"] = inbox[:per_source]

        info_items, _ = self.info.list_items(search=q, limit=per_source)
        results["info"] = [{
            "id": it.get("id"),
            "title": it.get("title_zh") or it.get("title", ""),
            "content_preview": _preview(it.get("summary_zh") or it.get("summary")),
            "source": it.get("source", ""), "url": it.get("url", ""),
            "_source": "info",
        } for it in info_items]

        vocab = []
        for w in self.vocab.search_words(q):
            vocab.append({
                "id": w.get("id"), "title": w.get("word", ""),
                "content_preview": _preview(w.get("meaning")),
                "wordbook": w.get("wordbook_name", ""),
                "_source": "vocab",
            })
        results["vocab"] = vocab

        # 结构化记忆（与后端 StructuredMemoryStore 共用 structured_memory_entries 键）
        memory = []
        entries = self.store.load_json("structured_memory_entries", [])
        if isinstance(entries, list):
            for e in entries:
                hay = (str(e.get("content", "")) + " " + str(e.get("supertag_type", "")) + " "
                       + " ".join(map(str, e.get("tags", [])))).lower()
                if ql in hay:
                    memory.append({
                        "id": e.get("id"),
                        "title": str(e.get("supertag_type", "")),
                        "content_preview": _preview(e.get("content")),
                        "tags": e.get("tags", []),
                        "updated_at": e.get("updated_at", ""),
                        "_source": "memory",
                    })
        results["memory"] = memory[:per_source]

        return results

    @staticmethod
    def _rrf_fuse(results, k: int = 60, limit: int = 15):
        """倒数排名融合（RRF），跨源排序。"""
        scores: dict[str, float] = {}
        items: dict[str, dict[str, Any]] = {}
        for source, items_list in results.items():
            for rank, item in enumerate(items_list):
                key = f"{item.get('_source')}:{item.get('id')}"
                if key not in items:
                    items[key] = item
                scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
        ordered = sorted(scores, key=scores.get, reverse=True)[:limit]
        fused = []
        for key in ordered:
            it = dict(items[key])
            it["_score"] = round(scores[key], 4)
            fused.append(it)
        return fused

    @staticmethod
    def _classify_intent(query):
        ql = (query or "").lower()
        scores = {cat: sum(1 for kw in kws if kw.lower() in ql)
                  for cat, kws in _INTENT_KEYWORDS.items()}
        best = max(scores, key=scores.get)
        return best if scores[best] else "auto"

    def knowledge_query(self, query, limit: int = 15) -> dict[str, Any]:
        """跨源知识检索（RO）：RRF 融合 + 意图识别。

        等效于 knowledge.unified_service 的本地数据层版本。因 MCP 隔离约束
        （禁止 import assistant/backend 及重依赖服务），直接复用 SiyiStore
        已接入的各 repository 门面，而非实例化完整 unified_service。
        """
        results = self._cross_search(query)
        fused = self._rrf_fuse(results, limit=max(1, min(int(limit or 15), 50)))
        return {
            "query": query,
            "intent": self._classify_intent(query),
            "total": len(fused),
            "results": fused,
            "sources": {name: items[:5] for name, items in results.items() if items},
        }

    def memory_recall(self, query, limit: int = 10) -> dict[str, Any]:
        """记忆召回（RO）：跨源融合 + 拼接 recall_context 供智能体引用。"""
        results = self._cross_search(query)
        fused = self._rrf_fuse(results, limit=max(1, min(int(limit or 10), 50)))
        lines = []
        for it in fused:
            src = it.get("_source", "")
            title = it.get("title", "")
            preview = it.get("content_preview", "")
            lines.append(f"[{src}] {title}: {preview}")
        return {
            "query": query,
            "total": len(fused),
            "results": fused,
            "recall_context": "\n".join(lines),
        }

    # ── 收件箱分类（无 AI 启发式，RO 分析）──────────────────────

    def inbox_classify(self, entry_id) -> dict[str, Any]:
        """对收件箱条目做启发式分类（RO）。

        受限说明：MCP 隔离进程不持有 AI 客户端，无法调用 inbox_classifier /
        AiTextService。此处用关键词启发式给出候选 category，仅作分析建议，
        不写入数据；若需应用分类，调用 siyi_inbox_update 写入 tags。
        """
        entry = self.inbox.get_entry(int(entry_id))
        if entry is None:
            return {"error": "not_found", "id": entry_id}
        tl = (str(entry.get("title", "")) + " " + str(entry.get("content", ""))).lower()
        scores = {cat: sum(1 for kw in kws if kw.lower() in tl)
                  for cat, kws in _CATEGORY_KEYWORDS.items()}
        if scores:
            best = max(scores, key=scores.get)
            confidence = min(1.0, 0.5 + 0.15 * scores[best])
        else:
            best, confidence = "note", 0.3
        return {
            "id": entry_id,
            "category": best,
            "confidence": round(confidence, 2),
            "scores": {k: v for k, v in scores.items() if v},
            "tags": list(entry.get("tags", [])),
            "reason": f"基于关键词命中推断为「{best}」(命中 {scores.get(best, 0)} 处)",
        }

    # ── 资讯 ──────────────────────────────────────────────────

    def news_list(self, limit: int = 15, unread_only: bool = False) -> dict[str, Any]:
        items, total = self.info.list_items(limit=max(1, min(int(limit or 15), 100)), read=False if unread_only else None)
        return {
            "total": total,
            "items": [
                {
                    "id": it.get("id"),
                    "title": it.get("title_zh") or it.get("title", ""),
                    "source": it.get("source", ""),
                    "url": it.get("url", ""),
                    "summary_preview": _preview(it.get("summary_zh") or it.get("summary")),
                    "collected_at": it.get("collected_at") or it.get("created_at", ""),
                    "read": bool(it.get("read")),
                }
                for it in items
            ],
        }

    def news_save(
        self,
        url: str,
        title: str,
        summary: str = "",
        source: str = "mcp",
        tags: list[str] | None = None,
        title_zh: str = "",
        summary_zh: str = "",
    ) -> dict[str, Any]:
        record = {
            "url": str(url or "").strip(),
            "title": str(title or "").strip(),
            "summary": str(summary or ""),
            "source": source or "mcp",
            "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
            "collected_at": _now_iso(),
        }
        if title_zh:
            record["title_zh"] = title_zh
        if summary_zh:
            record["summary_zh"] = summary_zh
        changed = self.info.upsert_items([record])
        return {"url": record["url"], "upserted": changed}

    # ── 通知（与 P2.9 NotificationDispatcher 同形状同键）────

    def notify(self, title: str, content: str, priority: str = "normal", event_type: str = "mcp.agent") -> dict[str, Any]:
        record = {
            "id": secrets.token_hex(8),
            "event_type": event_type or "mcp.agent",
            "title": str(title or "")[:200],
            "body": str(content or "")[:500],
            "priority": priority or "normal",
            "created_at": _now_iso(),
            "read": False,
            "raw": {"source": "mcp_server"},
        }
        with self._lock:
            records = self.store.load_json(KEY_NOTIFICATIONS, [])
            if not isinstance(records, list):
                records = []
            records.append(record)
            if len(records) > _MAX_NOTIFICATIONS:
                records = records[-_MAX_NOTIFICATIONS:]
            self.store.save_json(KEY_NOTIFICATIONS, records)
        return {"id": record["id"], "title": record["title"]}

    # ── 备份快照：内核自主触发全量备份 ──────────────────────────

    _BACKUP_KEYS = ("notes", "tasks", "settings", "notifications", "structured_memory_entries")

    def backup_snapshot(self, reason: str = "mcp.kernel") -> dict[str, Any]:
        """全量数据快照（RW）：收集核心数据键写入备份目录，返回备份清单。

        供内核在完成重要工作后自主触发持久化快照（如每日复盘归档、周备份）。
        数据从 SQLite（LocalDataStore.load_json）读取——与后端同源，
        不与 BackupService（backend 依赖）耦合，满足 MCP 隔离约束。
        """
        import os
        from pathlib import Path

        snapshot: dict[str, Any] = {"_meta": {"reason": reason or "mcp.kernel", "created_at": _now_iso()}}
        counts: dict[str, int] = {}
        for key in self._BACKUP_KEYS:
            try:
                value = self.store.load_json(key, None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("backup_snapshot: load %s failed: %s", key, exc)
                value = None
            if value is None:
                continue
            snapshot[key] = value
            counts[key] = len(value) if isinstance(value, list) else 1
        snapshot["_counts"] = counts

        backup_root = Path(self.data_dir) / "backups" / "snapshots"
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        rel_path = f"snapshot_{stamp}.json"
        target = backup_root / rel_path
        try:
            import json as _json

            with open(target, "w", encoding="utf-8") as f:
                f.write(_json.dumps(snapshot, ensure_ascii=False, indent=2))
        except OSError as exc:  # noqa: BLE001
            return {"status": "error", "reason": str(exc)[:200]}

        # 轮转：仅保留最近 10 份
        try:
            backups = sorted(backup_root.glob("snapshot_*.json"))
            for old in backups[:-10]:
                old.unlink(missing_ok=True)
        except OSError:
            pass
        return {
            "status": "ok",
            "file": str(target),
            "backup_id": f"snapshot_{stamp}",
            "keys": list(snapshot.keys()),
            "counts": counts,
        }

    # ── 记忆写入：内核持久化工作成果到结构化记忆 ─────────────────

    def memory_store(
        self,
        content: str,
        memory_type: str = "@知识",
        entity_type: str = "",
        source: str = "kernel",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """写入一条结构化记忆（RW）：内核把值得长期保留的成果/事实写入记忆库。

        与后端 StructuredMemoryStore 共用 ``structured_memory_entries`` 键和记录形状；
        MCP 隔离进程不 import memory/（后端层），直接复用 SiyiStore 门面按同形状追加。
        """
        content_s = str(content or "").strip()
        if not content_s:
            return {"error": "invalid_content", "detail": "content 不能为空"}
        supertag = str(memory_type or "").strip() or "@知识"
        entry: dict[str, Any] = {
            "id": secrets.token_hex(8),
            "supertag_type": supertag,
            "entity_type": entity_type or "",
            "relations": [],
            "source": source or "kernel",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
        }
        # 与 make_structured_entry 一致：field_values 直接并入顶层
        entry.update({"content": content_s})
        with self._lock:
            entries = self.store.load_json("structured_memory_entries", [])
            if not isinstance(entries, list):
                entries = []
            entries.append(entry)
            self.store.save_json("structured_memory_entries", entries)
        return {"id": entry["id"], "supertag_type": supertag, "created_at": entry["created_at"]}
