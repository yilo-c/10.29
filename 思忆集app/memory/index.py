from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from core.settings_schema import KEY_ASSISTANT_FOCUS_TOPICS, KEY_SEARCH_MODE

if TYPE_CHECKING:
    pass

logger = logging.getLogger("siyi.memory.index")


# ── Pattern extraction integration ────────────────────────────────────

try:
    from .extractor import PatternExtractor

    _PATTERN_EXTRACTOR_AVAILABLE = True
except ImportError:
    _PATTERN_EXTRACTOR_AVAILABLE = False
    PatternExtractor = None  # type: ignore


class AssistantMemoryIndexService:
    """统一可检索记忆索引：笔记、任务、收件箱、决策与执行记录。"""

    _DEFAULT_STATE: dict[str, Any] = {"entries": [], "built_at": None, "source_counts": {}}

    def __init__(
        self,
        *,
        data_store,
        settings_repository=None,
        user_profile_service=None,
        note_repository=None,
        task_repository=None,
        inbox_repository=None,
        info_item_repository=None,
        memory_search=None,
    ):
        self.data_store = data_store
        self.settings_repository = settings_repository
        self.user_profile_service = user_profile_service
        self.note_repository = note_repository
        self.task_repository = task_repository
        self.inbox_repository = inbox_repository
        self.info_item_repository = info_item_repository
        self._memory_search = memory_search  # Optional HybridMemorySearch or HybridSearchService instance
        # Check by class name to avoid importing services layer at runtime
        self._hybrid_search_service = (
            memory_search if memory_search is not None and type(memory_search).__name__ in ("HybridSearchService",) else None
        )
        self._pattern_extractor = PatternExtractor() if _PATTERN_EXTRACTOR_AVAILABLE else None
        self._pending_sync = False
        # 审查修复：保护 index_memory 的 read-modify-write 不被并发调用覆盖
        self._write_lock = threading.Lock()

    def build_recall_context(self, message: str = "", top_k: int = 4) -> str:
        """构建记忆召回上下文字符串。

        当提供 message 时：根据用户消息检索相关记忆，返回格式化的 recall 上下文。
        当不提供 message 时：构建全局长期记忆上下文（替代 MemoryManager.build_memory_context()），
        包含用户画像、近期关注话题和关键记忆条目。
        """
        # ── 基于消息的召回（现有行为）──
        if str(message or "").strip():
            try:
                result = self.recall(message, top_k=top_k)
            except Exception:
                logger.debug("memory recall failed for context", exc_info=True)
                return ""
            items = list(result.get("items") or [])[:top_k]
            if not items:
                return ""
            lines = ["以下是本地可检索记忆：", ""]
            for item in items:
                lines.append(f"- [{item.get('source_type')}] {item.get('title')}：{item.get('snippet') or ''}".strip())
            return "\n".join(lines)

        # ── 全局长期记忆上下文（替代 MemoryManager.build_memory_context()）──
        parts = []

        # 1. 用户画像摘要
        if self.user_profile_service is not None:
            try:
                summary = self.user_profile_service.build_planner_summary()
                if summary and summary.strip():
                    parts.append(f"【用户画像】{str(summary).strip()}")
            except Exception:
                logger.warning("failed to build user profile summary")

        # 2. 近期关注话题
        try:
            topics = self.get_focus_topics()
            if topics:
                parts.append(f"【近期关注】{'、'.join(str(t) for t in topics[:5])}")
        except Exception:
            logger.warning("failed to get focus topics")

        # 3. 关键记忆条目
        try:
            memories = self.recall(query="", top_k=10)
            items = list(memories.get("items") or [])
            if items:
                memory_texts = []
                for m in items[:5]:
                    snippet = str(m.get("snippet") or "")
                    if snippet:
                        memory_texts.append(snippet)
                if memory_texts:
                    parts.append("【相关记忆】" + " | ".join(memory_texts))
        except Exception:
            logger.warning("failed to recall memories")

        return "\n".join(parts) if parts else "暂无长期记忆记录。"

    def get_focus_topics(self) -> list[str]:
        if self.user_profile_service is not None:
            try:
                topics = self.user_profile_service.get_focus_topics()
                if isinstance(topics, list):
                    normalized = [str(item).strip() for item in topics if str(item).strip()]
                    if normalized:
                        return normalized
            except Exception as exc:
                logger.info("memory index skipped user profile topics: %s", exc, exc_info=True)
        if self.settings_repository is None:
            return []
        topics = self.settings_repository.get_setting(KEY_ASSISTANT_FOCUS_TOPICS, [])
        if isinstance(topics, list):
            return [str(item).strip() for item in topics if str(item).strip()]
        if isinstance(topics, str):
            return [line.strip() for line in topics.splitlines() if line.strip()]
        return []

    def rebuild_index(self) -> dict[str, Any]:
        # U3（S3-5，2026-09-16）：rebuild 是全量覆盖写——旧实现会把
        # revoke_memory 打上的软删标记连同索引一起冲掉（被撤销的记忆在
        # 下一次 rebuild 后"复活"）。故先收集旧 entries 的 revoked 键集，
        # 重建后按 entry_id 继承软删标记。
        with self._write_lock:
            prev = self.data_store.load_json("assistant_memory_index", self._DEFAULT_STATE)
            revoked_ids = set()
            if isinstance(prev, dict):
                for e in prev.get("entries") or []:
                    if isinstance(e, dict) and (e.get("metadata") or {}).get("revoked"):
                        revoked_ids.add(str(e.get("entry_id") or ""))
            entries = list(self._collect_entries())
        if revoked_ids:
            for entry in entries:
                if str(entry.get("entry_id") or "") in revoked_ids:
                    meta = dict(entry.get("metadata") or {})
                    meta["revoked"] = True
                    entry["metadata"] = meta
        source_counts: dict[str, int] = {}
        for entry in entries:
            source = str(entry.get("source_type") or "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1
        state = {
            "entries": entries,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "source_counts": source_counts,
        }
        self.data_store.save_json("assistant_memory_index", state)
        self._sync_memory_search()
        return state

    def recall(
        self,
        query: str,
        *,
        top_k: int = 8,
        source_filters: list[str] | None = None,
        days: int | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        rebuild_if_missing: bool = True,
        search_mode: str | None = None,
    ) -> dict[str, Any]:
        normalized_query = str(query or "").strip()
        effective_query = normalized_query or " ".join(self.get_focus_topics()[:3]).strip()

        # 如果配置了混合搜索引擎，优先使用
        if self._memory_search is not None:
            return self._hybrid_recall(
                query=normalized_query,
                effective_query=effective_query,
                top_k=top_k,
                source_filters=source_filters,
                days=days,
                start_at=start_at,
                end_at=end_at,
                search_mode=search_mode,
            )

        # 向后兼容：使用现有 FTS 搜索
        state = self.data_store.load_json("assistant_memory_index", self._DEFAULT_STATE)
        rebuilt = False
        if not isinstance(state, dict) or not isinstance(state.get("entries"), list):
            state = dict(self._DEFAULT_STATE)
        if rebuild_if_missing and not state.get("entries"):
            state = self.rebuild_index()
            rebuilt = True
        entries = list(state.get("entries") or [])
        entries = self._filter_entries(entries, source_filters=source_filters, days=days, start_at=start_at, end_at=end_at)
        if effective_query:
            ranked = self.data_store.fts_search_items(
                entries,
                effective_query,
                ["title", "content", "summary", "source_type", "tags_text"],
                max_results=max(1, min(top_k, 20)) * 3,
            )
        else:
            ranked = entries
        items = [self._format_entry(entry, effective_query) for entry in ranked[: max(1, min(top_k, 20))]]
        # 为向后兼容的结果添加来源标签
        items = [self._decorate_with_source_label(item) for item in items]
        return {
            "query": normalized_query,
            "effective_query": effective_query,
            "count": len(items),
            "built_at": state.get("built_at"),
            "source_counts": dict(state.get("source_counts") or {}),
            "mode": "legacy_fts",
            "rebuilt": rebuilt,
            "items": items,
        }

    def find_related(
        self,
        text: str,
        limit: int = 5,
        min_score: float = 0.3,
    ) -> list[dict[str, Any]]:
        """根据文本内容查找相关记忆（非模态浮现用）。"""
        if not text or not text.strip():
            return []
        # 教训（P0 案 H）：`is not None` 守卫只能证明「对象存在」，不能证明
        # 「契约存在」——装配时 _memory_search 可能被塞进缺 find_related 方法的
        # 实现（如曾经的 HybridSearchService），此时假守卫永不短路、每次都抛
        # AttributeError 被上层吞成 -32603。故真降级用 try/except AttributeError
        # 兜底，而非依赖对象非空判断。
        if self._memory_search is not None:
            try:
                return self._memory_search.find_related(text, limit=limit, min_score=min_score)
            except AttributeError:
                logger.warning(
                    "memory_search %s lacks find_related, falling back to recall()",
                    type(self._memory_search).__name__,
                    exc_info=True,
                )
        # fallback: 用 recall 代替
        result = self.recall(text, top_k=limit)
        return result.get("items", [])[:limit]

    def _hybrid_recall(
        self,
        *,
        query: str,
        effective_query: str,
        top_k: int,
        source_filters: list[str] | None,
        days: int | None,
        start_at: str | None,
        end_at: str | None,
        search_mode: str | None,
    ) -> dict[str, Any]:
        """使用混合搜索引擎进行检索。

        优先使用 HybridSearchService（包装了 EventBus 和降级逻辑），
        回退到原始 HybridMemorySearch。
        """
        engine = self._hybrid_search_service or self._memory_search
        if engine is None:
            return {"query": query, "items": [], "count": 0, "mode": "none"}

        # 解析 search_mode
        if search_mode is None and self.settings_repository is not None:
            search_mode = self.settings_repository.get_setting(KEY_SEARCH_MODE, "hybrid")
        search_mode = str(search_mode or "hybrid").strip().lower()
        if search_mode not in ("hybrid", "vector", "keyword"):
            search_mode = "hybrid"

        # 若搜索引擎无数据，先同步
        entry_count = (
            self._hybrid_search_service.get_entry_count()
            if self._hybrid_search_service is not None
            else self._memory_search.get_entry_count()
            if self._memory_search is not None
            else 0
        )
        if entry_count == 0:
            self._sync_memory_search()
        elif self._pending_sync:
            self._sync_memory_search()

        # 构建时间范围
        time_range = None
        if start_at or end_at:
            time_range = (
                self._parse_datetime(start_at) if start_at else None,
                self._parse_datetime(end_at) if end_at else None,
            )
        elif days:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=max(1, days))
            time_range = (start, end)

        # 执行检索（优先使用 HybridSearchService.search）
        if self._hybrid_search_service is not None:
            result = self._hybrid_search_service.search(
                query=query or effective_query,
                top_k=top_k,
                search_mode=search_mode,
                source_filters=source_filters,
                time_range=time_range,
            )
        else:
            result = self._memory_search.search(
                query=query or effective_query,
                top_k=top_k,
                search_mode=search_mode,
                source_filters=source_filters,
                time_range=time_range,
            )

        # 格式化 items
        items = []
        for item in result.get("items") or []:
            items.append(self._format_entry(item, result.get("effective_query", query)))

        return {
            "query": query,
            "effective_query": result.get("effective_query", query),
            "mode": result.get("mode", search_mode),
            "count": len(items),
            "items": items,
            "sources": result.get("sources", []),
            "time_range": result.get("time_range"),
        }

    def _sync_memory_search(self) -> None:
        """将当前索引条目同步到混合搜索引擎。"""
        if self._memory_search is None:
            return
        try:
            state = self.data_store.load_json("assistant_memory_index", self._DEFAULT_STATE)
            # U3（S3-5）：软删（revoked）条目不得进入检索引擎，否则
            # revoke_memory 只挡得住 legacy FTS 路径、hybrid 路径照常召回。
            entries = [e for e in (state.get("entries") or []) if not (isinstance(e, dict) and (e.get("metadata") or {}).get("revoked"))]
            all_entries = list(state.get("entries") or [])
            if entries or all_entries:
                # 即便过滤后为空（全部撤销）也要调用 sync_entries([]) 清空引擎，
                # 否则旧数据残留可检索。
                self._memory_search.sync_entries(entries)
                logger.info("Synced %d entries to hybrid search engine", len(entries))
            self._pending_sync = False
        except Exception as exc:
            logger.warning("Memory search sync failed: %s", exc, exc_info=True)

    @staticmethod
    def _decorate_with_source_label(item: dict[str, Any]) -> dict[str, Any]:
        """为条目添加来源标签（向后兼容路径）。"""
        source_type = str(item.get("source_type") or "")
        try:
            from .search import get_source_color, get_source_label

            item["source_label"] = get_source_label(source_type)
            item["source_color"] = get_source_color(source_type)
        except ImportError:
            item["source_label"] = f"[{source_type}]" if source_type else "[记忆]"
            item["source_color"] = "#95A5A6"
        return item

    def _collect_entries(self) -> Iterable[dict[str, Any]]:
        if self.note_repository is not None:
            try:
                for note in self.note_repository.list_notes(include_deleted=False):
                    yield self._build_entry(
                        source_type="note",
                        source_id=note.get("id"),
                        title=note.get("title") or "未命名笔记",
                        content=note.get("content") or "",
                        updated_at=note.get("updated_at") or note.get("created_at"),
                        metadata={"folder_path": note.get("folder_path") or "/", "tags": list(note.get("tags") or [])},
                    )
            except Exception as exc:
                logger.info("memory index skipped note repository: %s", exc, exc_info=True)
        if self.task_repository is not None:
            try:
                for task in self.task_repository.list_tasks(include_deleted=False):
                    title = task.get("title") or task.get("name") or "未命名任务"
                    content = task.get("description") or task.get("content") or task.get("notes") or ""
                    metadata = {
                        "status": task.get("status") or ("completed" if task.get("is_completed") else "pending"),
                        "priority": task.get("priority") or "",
                    }
                    yield self._build_entry(
                        source_type="task",
                        source_id=task.get("id"),
                        title=title,
                        content=content,
                        updated_at=task.get("updated_at") or task.get("created_at"),
                        metadata=metadata,
                    )
            except Exception as exc:
                logger.info("memory index skipped task repository: %s", exc, exc_info=True)
        if self.inbox_repository is not None:
            try:
                for entry in self.inbox_repository.list_entries(include_archived=True):
                    metadata = {
                        "status": entry.get("status") or "new",
                        "source": entry.get("source") or "manual",
                        "tags": list(entry.get("tags") or []),
                    }
                    yield self._build_entry(
                        source_type="inbox",
                        source_id=entry.get("id"),
                        title=entry.get("title") or "未命名收件",
                        content=entry.get("content") or "",
                        updated_at=entry.get("updated_at") or entry.get("created_at"),
                        metadata=metadata,
                    )
            except Exception as exc:
                logger.info("memory index skipped inbox repository: %s", exc, exc_info=True)
        if self.info_item_repository is not None:
            try:
                info_items, _ = self.info_item_repository.list_items(limit=300)
                for item in info_items:
                    metadata = {
                        "source": item.get("source", ""),
                        "category": item.get("category", "general"),
                        "tags": list(item.get("tags") or []),
                        "url": item.get("url", ""),
                        "saved": bool(item.get("saved", False)),
                    }
                    content_parts = []
                    if item.get("summary_zh"):
                        content_parts.append(str(item["summary_zh"]))
                    elif item.get("summary"):
                        content_parts.append(str(item["summary"]))
                    if item.get("title_zh") and item.get("title"):
                        content_parts.append(f"原标题: {item['title']}")
                    content = "\n".join(content_parts)
                    yield self._build_entry(
                        source_type="info",
                        source_id=item.get("id"),
                        title=item.get("title_zh") or item.get("title") or "未命名资讯",
                        content=content,
                        updated_at=item.get("collected_at") or item.get("created_at"),
                        metadata=metadata,
                    )
            except Exception as exc:
                logger.info("memory index skipped info item repository: %s", exc, exc_info=True)
        for decision in self.data_store.load_json("assistant_decisions", []):
            if not isinstance(decision, dict):
                continue
            steps = decision.get("steps") or []
            summary = decision.get("summary") or {}
            step_text = ", ".join(str(step.get("action_id") or "") for step in steps if isinstance(step, dict))
            content = json.dumps(summary, ensure_ascii=False)
            if step_text:
                content = f"steps={step_text}\n{content}"
            yield self._build_entry(
                source_type="decision",
                source_id=decision.get("time") or decision.get("goal"),
                title=decision.get("goal") or "助手决策",
                content=content,
                updated_at=decision.get("time"),
                metadata={"step_count": len(steps)},
            )
        for run in self.data_store.load_json("assistant_execution_runs", []):
            if not isinstance(run, dict):
                continue
            title = run.get("goal") or run.get("run_id") or "助手执行记录"
            content = json.dumps(run.get("summary") or run, ensure_ascii=False)
            yield self._build_entry(
                source_type="execution_run",
                source_id=run.get("run_id") or run.get("started_at") or title,
                title=title,
                content=content,
                updated_at=run.get("updated_at") or run.get("finished_at") or run.get("started_at"),
                metadata={"status": run.get("status") or "unknown"},
            )

    @staticmethod
    def _build_entry(
        *, source_type: str, source_id: Any, title: str, content: str, updated_at: Any, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        meta = dict(metadata or {})
        tags = [str(item).strip() for item in (meta.get("tags") or []) if str(item).strip()]
        summary_parts = []
        for key in ("status", "source", "priority", "folder_path"):
            value = str(meta.get(key) or "").strip()
            if value:
                summary_parts.append(value)
        return {
            "entry_id": f"{source_type}:{source_id}",
            "source_type": str(source_type),
            "source_id": source_id,
            "title": str(title or "").strip(),
            "content": str(content or "").strip(),
            "summary": " | ".join(summary_parts),
            "tags_text": " ".join(tags),
            "updated_at": str(updated_at or "").strip(),
            "metadata": meta,
        }

    def _filter_entries(
        self,
        entries: list[dict[str, Any]],
        *,
        source_filters: list[str] | None,
        days: int | None,
        start_at: str | None,
        end_at: str | None,
    ) -> list[dict[str, Any]]:
        allowed_sources = {str(item).strip() for item in (source_filters or []) if str(item).strip()}
        start_dt = self._parse_datetime(start_at)
        end_dt = self._parse_datetime(end_at)
        if days:
            fallback_start = datetime.now(timezone.utc) - timedelta(days=max(1, days))
            start_dt = max(filter(None, [start_dt, fallback_start]), default=fallback_start)
        filtered: list[dict[str, Any]] = []
        for entry in entries:
            # U3（S3-5）：软删条目对所有召回路径不可见（legacy FTS 路径）。
            if (entry.get("metadata") or {}).get("revoked"):
                continue
            if allowed_sources and str(entry.get("source_type") or "") not in allowed_sources:
                continue
            entry_dt = self._parse_datetime(entry.get("updated_at"))
            if start_dt and entry_dt and entry_dt < start_dt:
                continue
            if end_dt and entry_dt and entry_dt > end_dt:
                continue
            filtered.append(entry)
        return filtered

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        for candidate in (text.replace("Z", "+00:00"), text):
            try:
                dt = datetime.fromisoformat(candidate)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        for fmt, length in (("%Y-%m-%d %H:%M", 16), ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d", 10)):
            try:
                return datetime.strptime(text[:length], fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    # ── Pattern extraction integration (D-SUP2) ─────────────────────

    def get_pattern_extractor(self):
        """获取 PatternExtractor 实例（如果可用）。"""
        return self._pattern_extractor

    def extract_patterns_from_conversations(
        self,
        conversations: list[dict[str, Any]],
        min_confidence: float = 0.3,
    ) -> list[dict[str, Any]]:
        """从对话中提取行为模式并存入索引。

        Args:
            conversations: 对话历史列表。
            min_confidence: 最低置信度阈值。

        Returns:
            提取的模式列表。
        """
        if self._pattern_extractor is None:
            logger.info("PatternExtractor not available (assistant_memory_extractor not loaded)")
            return []

        patterns = self._pattern_extractor.extract_patterns(
            conversations=conversations,
            min_confidence=min_confidence,
        )

        # Store patterns as memory index entries
        if patterns:
            stored = 0
            for p in patterns:
                entry = self._build_entry(
                    source_type="pattern",
                    source_id=f"pattern:{p.get('pattern', 'unknown')}:{datetime.now(timezone.utc).isoformat()}",
                    title=p.get("pattern", "行为模式"),
                    content=_format_pattern_content(p),
                    updated_at=datetime.now(timezone.utc).isoformat(),
                    metadata={
                        "pattern_type": p.get("type", "unknown"),
                        "confidence": p.get("confidence", 0),
                        "tags": list(p.get("tags", [])),
                    },
                )
                # Write directly to stored state
                try:
                    state = self.data_store.load_json("assistant_memory_index", self._DEFAULT_STATE)
                    if not isinstance(state, dict):
                        state = dict(self._DEFAULT_STATE)
                    entries = list(state.get("entries") or [])
                    entries.append(entry)
                    state["entries"] = entries
                    state["built_at"] = datetime.now(timezone.utc).isoformat()
                    src_counts = dict(state.get("source_counts") or {})
                    src_counts["pattern"] = src_counts.get("pattern", 0) + 1
                    state["source_counts"] = src_counts
                    self.data_store.save_json("assistant_memory_index", state)
                    stored += 1
                except Exception as exc:
                    logger.warning("pattern store failed: %s", exc, exc_info=True)

            logger.info("Stored %d/%d extracted patterns", stored, len(patterns))
            if stored:
                self._sync_memory_search()

        return patterns

    def get_stored_patterns(
        self,
        pattern_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """从记忆索引中检索已存储的行为模式。

        Args:
            pattern_type: 可选的模式类型过滤。
            limit: 返回上限。

        Returns:
            模式条目列表。
        """
        state = self.data_store.load_json("assistant_memory_index", self._DEFAULT_STATE)
        entries = list(state.get("entries") or [])
        pattern_entries = [e for e in entries if str(e.get("source_type", "")) == "pattern"]
        if pattern_type:
            pattern_entries = [e for e in pattern_entries if str(e.get("metadata", {}).get("pattern_type", "")) == pattern_type]
        return pattern_entries[-limit:]

    # ── Memory entry persistence (Phase 5) ────────────────────────────

    def index_memory(self, candidate_dict: dict[str, Any]) -> dict[str, Any] | None:
        """Persist an extracted memory candidate to the index.

        Args:
            candidate_dict: Dict from MemoryCandidate.to_dict().

        Returns:
            The created index entry, or None on failure.
        """
        content = str(candidate_dict.get("content") or "").strip()
        if not content:
            return None

        mem_type = str(candidate_dict.get("type") or "factual")
        mem_id = (
            f"mem_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_"
            f"{hashlib.md5(content.encode('utf-8', errors='replace'), usedforsecurity=False).hexdigest()[:8]}"
        )
        privacy_level = str(candidate_dict.get("privacy_level") or "")

        entry = self._build_entry(
            source_type="memory",
            source_id=mem_id,
            title=f"[{mem_type}] {content[:80]}",
            content=content,
            updated_at=datetime.now(timezone.utc).isoformat(),
            metadata={
                "memory_type": mem_type,
                "confidence": candidate_dict.get("confidence", 0.0),
                "source": candidate_dict.get("source", "user"),
                "tags": list(candidate_dict.get("tags") or []),
                "reason": str(candidate_dict.get("reason") or ""),
                "privacy_level": privacy_level,
            },
        )

        try:
            # 审查修复：保护 read-modify-write（load_json → append → save_json），
            # 防止 data_import_chat_folder 并行导入时多线程覆盖丢失记忆条目。
            with self._write_lock:
                state = dict(self.data_store.load_json("assistant_memory_index", self._DEFAULT_STATE))
                entries = list(state.get("entries") or [])
                entries.append(entry)
                state["entries"] = entries
                state["built_at"] = datetime.now(timezone.utc).isoformat()
                src_counts = dict(state.get("source_counts") or {})
                src_counts["memory"] = src_counts.get("memory", 0) + 1
                state["source_counts"] = src_counts
                self.data_store.save_json("assistant_memory_index", state)
                # 修复 B14: 原实现仅设置 _pending_sync=True，依赖 _hybrid_recall 触发同步。
                # 若 index_memory 后从未调用 recall，同步永远不会发生，导致搜索引擎与索引不一致。
                # 改为保存后直接同步（与 extract_patterns_from_conversations 行为一致）。
                self._sync_memory_search()
            logger.info(
                "Indexed memory [%s]: %s (privacy=%s)",
                mem_type,
                content[:60],
                privacy_level or "unchecked",
            )
            return dict(entry)
        except Exception as exc:
            logger.warning("Failed to index memory: %s", exc, exc_info=True)
            return None

    def revoke_memory(
        self,
        entry_id: str,
        reason: str = "",
        revoked_by: str = "user",
    ) -> bool:
        """Soft-delete a memory entry with audit trail.

        Marks the entry as revoked and appends a revocation audit record.
        The entry remains in the index (not hard-deleted) for recoverability.

        Args:
            entry_id: The source_id of the memory entry to revoke.
            reason: Why the memory was revoked.
            revoked_by: Who initiated the revocation ("user" | "system" | "judge").

        Returns:
            True if the entry was found and revoked, False otherwise.
        """
        with self._write_lock:
            try:
                state = self.data_store.load_json("assistant_memory_index", self._DEFAULT_STATE)
                if not isinstance(state, dict):
                    return False
                entries = copy.deepcopy(state.get("entries") or [])
                found = False
                for entry in entries:
                    # 直接匹配 entry_id/source_id，或按 source_id 后缀在 memory 条目中兜底匹配。
                    # 合并为单一条件，简化缩进，避免原实现中首个条目命中 break 后
                    # 跳过保存/审计/返回的"只处理/不保存"逻辑错误（B 修复）。
                    if (entry.get("entry_id") == entry_id or entry.get("source_id") == entry_id) or (
                        entry.get("source_type") == "memory" and str(entry.get("source_id", "")).endswith(entry_id)
                    ):
                        # 防御：entry 缺失 metadata 时初始化为空 dict
                        meta = dict(entry.get("metadata") or {})
                        meta["revoked"] = True
                        meta["revoked_at"] = datetime.now(timezone.utc).isoformat()
                        meta["revoke_reason"] = str(reason or "")
                        meta["revoked_by"] = str(revoked_by)
                        entry["metadata"] = meta
                        entry["updated_at"] = meta["revoked_at"]
                        found = True
                        break

                if not found:
                    logger.warning("revoke_memory: entry not found: %s", entry_id)
                    return False

                state["entries"] = entries
                state["built_at"] = datetime.now(timezone.utc).isoformat()
                self.data_store.save_json("assistant_memory_index", state)
                # U3（S3-5）：撤销后必须让 hybrid 引擎重新同步（剔除 revoked），
                # 否则 legacy FTS 挡住了、引擎路径照常召回——软删只删了一半。
                self._pending_sync = True

                # 追加审计记录
                self._append_audit_log(
                    "revoke",
                    entry_id,
                    {
                        "reason": reason,
                        "revoked_by": revoked_by,
                    },
                )

                logger.info("Revoked memory entry: %s (reason=%s)", entry_id, reason or "unspecified")
                return True
            except Exception as exc:
                logger.warning("Failed to revoke memory: %s", exc, exc_info=True)
                return False

    def restore_memory(self, entry_id: str, reason: str = "", restored_by: str = "user") -> bool:
        """U3-batch1（S3-5 面板案，2026-09-16）：恢复被软删（revoked）的条目。

        与 revoke_memory 完全对称：清 revoked 标记族 + 审计 + _pending_sync。
        恢复后条目经 _sync_memory_search 重新进入 hybrid 引擎（软删过滤已修），
        召回自动命中——不开新的状态源。

        匹配语义与 revoke 一致（entry_id/source_id 直匹 + memory 条目后缀兜底）。
        未撤销过的条目返回 False（不误报成功、不改写时间戳）。
        """
        with self._write_lock:
            try:
                state = self.data_store.load_json("assistant_memory_index", self._DEFAULT_STATE)
                if not isinstance(state, dict):
                    return False
                entries = copy.deepcopy(state.get("entries") or [])
                found = False
                for entry in entries:
                    if (entry.get("entry_id") == entry_id or entry.get("source_id") == entry_id) or (
                        entry.get("source_type") == "memory" and str(entry.get("source_id", "")).endswith(entry_id)
                    ):
                        meta = dict(entry.get("metadata") or {})
                        if not meta.get("revoked"):
                            return False  # 未撤销过的条目，恢复无意义
                        now = datetime.now(timezone.utc).isoformat()
                        for key in ("revoked", "revoked_at", "revoke_reason", "revoked_by"):
                            meta.pop(key, None)
                        meta["restored_at"] = now
                        meta["restored_by"] = str(restored_by)
                        entry["metadata"] = meta
                        entry["updated_at"] = now
                        found = True
                        break
                if not found:
                    logger.warning("restore_memory: entry not found: %s", entry_id)
                    return False
                state["entries"] = entries
                state["built_at"] = datetime.now(timezone.utc).isoformat()
                self.data_store.save_json("assistant_memory_index", state)
                # 与 revoke 同理：状态变了必须触发引擎重同步，否则 JSON 里
                # 恢复了、hybrid 召回仍缺这条（双状态面）。
                self._pending_sync = True
                self._append_audit_log(
                    "restore",
                    entry_id,
                    {"reason": reason, "restored_by": restored_by},
                )
                logger.info("Restored memory entry: %s (reason=%s)", entry_id, reason or "unspecified")
                return True
            except Exception as exc:
                logger.warning("Failed to restore memory: %s", exc, exc_info=True)
                return False

    def list_entries(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        include_revoked: bool = False,
    ) -> dict[str, Any]:
        """U3-batch1（S3-5 面板案）：全量条目分页列表（updated_at 倒序）。

        专供「记忆管理面板」——**不走 recall**（memory.recent 的空查询兜底是
        focus-topics 相关性召回，语义名不副实，审计 §10.1）。直读索引 JSON：

        - 真「最近」= updated_at 字符串倒序（ISO8601 可直接字典序比较）；
        - 真分页 = limit/offset，无 recall 的 min(top_k,20) 硬上限；
        - revoked 条目默认隐藏，include_revoked=True 时带 metadata.revoked 标记返回
          （面板「已撤销(可恢复)」tab 的数据源）。

        Returns: {"items": [...], "total": n_all, "total_revoked": n_revoked}
        items 经 _format_entry 归一（entry_id 补写、snippet、metadata 原样透出）。
        """
        try:
            lim = max(1, min(int(limit or 50), 200))
        except (TypeError, ValueError):
            lim = 50
        try:
            off = max(0, int(offset or 0))
        except (TypeError, ValueError):
            off = 0
        try:
            state = self.data_store.load_json("assistant_memory_index", self._DEFAULT_STATE)
            if not isinstance(state, dict):
                state = dict(self._DEFAULT_STATE)
            entries = list(state.get("entries") or [])
        except Exception:
            logger.exception("list_entries: 索引状态读取失败")
            return {"items": [], "total": 0, "total_revoked": 0}

        def _revoked(e: dict) -> bool:
            return bool((e.get("metadata") or {}).get("revoked"))

        revoked_count = sum(1 for e in entries if _revoked(e))
        pool = entries if include_revoked else [e for e in entries if not _revoked(e)]
        pool = sorted(pool, key=lambda e: str(e.get("updated_at") or ""), reverse=True)
        page = pool[off : off + lim]
        return {
            "items": [self._format_entry(e, "") for e in page],
            "total": len(pool),
            "total_revoked": revoked_count,
        }

    def delete_entries_by_source(
        self,
        source_type: str,
        source_id: str | int,
    ) -> int:
        """P1.5: 按 source_type + source_id 级联删除记忆条目。

        删除 ``assistant_memory_index`` JSON 状态中所有匹配条目，
        并同步从 HybridSearchService（ChromaDB + FTS）中删除对应 entry_id。

        Args:
            source_type: "note" / "task" / "inbox" / "info" / "memory" 等
            source_id: 来源 ID（笔记 ID / 任务 ID / ...）

        Returns:
            实际删除的条目数
        """
        sid = str(source_id)
        stype = str(source_type or "").strip()
        if not stype:
            return 0
        try:
            state = self.data_store.load_json("assistant_memory_index", self._DEFAULT_STATE)
            if not isinstance(state, dict):
                return 0
            entries = list(state.get("entries") or [])
            removed_entry_ids: list[str] = []
            kept: list[dict[str, Any]] = []
            for entry in entries:
                if str(entry.get("source_type") or "") == stype and str(entry.get("source_id") or "") == sid:
                    removed_entry_ids.append(str(entry.get("entry_id") or f"{stype}:{sid}"))
                else:
                    kept.append(entry)
            if not removed_entry_ids:
                return 0
            state["entries"] = kept
            state["built_at"] = datetime.now(timezone.utc).isoformat()
            self.data_store.save_json("assistant_memory_index", state)
            # 同步删除混合搜索（ChromaDB / FTS）索引
            if self._hybrid_search_service is not None:
                for entry_id in removed_entry_ids:
                    try:
                        self._hybrid_search_service.delete_entry(entry_id)
                    except Exception as exc:
                        logger.debug("hybrid_search.delete_entry(%s) failed: %s", entry_id, exc)
            self._append_audit_log(
                "cascade_delete_by_source",
                f"{stype}:{sid}",
                {"removed_count": len(removed_entry_ids)},
            )
            logger.info(
                "Cascade-deleted %d memory entries for %s:%s",
                len(removed_entry_ids),
                stype,
                sid,
            )
            return len(removed_entry_ids)
        except Exception as exc:
            logger.warning(
                "delete_entries_by_source failed for %s:%s: %s",
                stype,
                sid,
                exc,
                exc_info=True,
            )
            return 0

    def get_memories_by_type(
        self,
        memory_type: str,
        *,
        include_revoked: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Retrieve memory entries filtered by memory type.

        Args:
            memory_type: One of the MEMORY_TYPE_* constants (factual, preference, etc.).
            include_revoked: If True, include soft-deleted entries.
            limit: Max entries to return.

        Returns:
            List of matching index entries.
        """
        try:
            state = self.data_store.load_json("assistant_memory_index", self._DEFAULT_STATE)
            entries = list(state.get("entries") or [])
        except Exception:
            return []

        matched = []
        for e in entries:
            if e.get("source_type") != "memory":
                continue
            if not include_revoked and (e.get("metadata") or {}).get("revoked"):
                continue
            if (e.get("metadata") or {}).get("memory_type") == memory_type:
                matched.append(self._format_entry(e, ""))

        return matched[-limit:]

    def get_audit_log(
        self,
        *,
        limit: int = 100,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve memory audit log entries.

        Args:
            limit: Max entries to return.
            action: Optional filter (e.g. "revoke", "edit", "confirm").

        Returns:
            List of audit log entries, most recent first.
        """
        try:
            log = self.data_store.load_json("assistant_memory_audit_log", [])
            if not isinstance(log, list):
                return []
        except Exception:
            return []

        if action:
            log = [e for e in log if isinstance(e, dict) and e.get("action") == action]

        return [dict(e) for e in log[-limit:]]

    # ── Internal helpers ─────────────────────────────────────────────

    def _append_audit_log(self, action: str, entry_id: str, details: dict[str, Any]) -> None:
        """Append an audit record to the memory audit log."""
        record = {
            "action": action,
            "entry_id": entry_id,
            "details": dict(details),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:

            def _updater(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
                normalized = list(log) if isinstance(log, list) else []
                normalized.append(record)
                return normalized[-1000:]  # keep last 1000 records

            self.data_store.update_json("assistant_memory_audit_log", [], _updater)
        except Exception as exc:
            logger.warning("Failed to append audit log: %s", exc, exc_info=True)

    @staticmethod
    def _format_entry(entry: dict[str, Any], query: str) -> dict[str, Any]:
        content = str(entry.get("content") or "")
        snippet = AssistantMemoryIndexService._build_snippet(content, query)
        # U3（S3-5，"entry_id 补写小项"真相）：hybrid 引擎 search 返回的
        # 条目只有 id 没有 entry_id（legacy JSON 条目则相反），不补写的话
        # 前端拿到 entry_id=None，删除/溯源按钮永远点不动。统一归一。
        entry_id = entry.get("entry_id") or entry.get("id")
        return {
            "entry_id": entry_id,
            "source_type": entry.get("source_type"),
            "source_id": entry.get("source_id"),
            "title": entry.get("title"),
            "snippet": snippet,
            "updated_at": entry.get("updated_at"),
            "metadata": dict(entry.get("metadata") or {}),
        }

    @staticmethod
    def _build_snippet(text: str, query: str, limit: int = 220) -> str:
        normalized = str(text or "").replace("\r\n", "\n").strip()
        if not normalized:
            return ""
        query_text = str(query or "").strip().lower()
        if query_text:
            lower_text = normalized.lower()
            position = lower_text.find(query_text)
            if position >= 0:
                start = max(0, position - 48)
                end = min(len(normalized), position + len(query_text) + 140)
                return normalized[start:end].replace("\n", " ")[:limit]
        return normalized.replace("\n", " ")[:limit]


# ── Helpers (module level) ────────────────────────────────────────────


def _format_pattern_content(pattern: dict[str, Any]) -> str:
    """将 PatternExtractor 的模式格式化为可存储的内容字符串。"""
    parts = [
        f"类型: {pattern.get('type', 'unknown')}",
        f"模式: {pattern.get('pattern', '')}",
        f"置信度: {pattern.get('confidence', 0)}",
    ]
    evidence = pattern.get("evidence")
    if evidence and isinstance(evidence, list):
        parts.append("证据: " + " | ".join(str(e) for e in evidence[:3]))
    skill = pattern.get("skill")
    if skill:
        parts.append(f"技能: {skill}")
    return " | ".join(parts)
