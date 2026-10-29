"""行为事件→记忆提取器 — 全量记忆闭环核心。

将行为日志（文件操作、浏览器访问、App 状态）转换为结构化记忆候选，
经过隐私过滤和去重后，写入记忆调度系统。这是 Phase 2b 的全量记忆闭环组件。

工作流：
  BehaviorLogger.query() → 规则分类 → MemoryCandidate → PrivacyFilter →
  DedupEngine → MemoryScheduler.add_memory()

设计原则：
- 规则驱动为主（LLM 增强为可选），保持低延迟和低成本
- 隐私过滤复用 extractor.PrivacyFilter，保证一致性
- 行为记忆的置信度天然低于对话记忆，默认流向待确认队列
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from memory.extractor import DedupEngine, PrivacyFilter
from memory.scheduler import MemoryScheduler
from memory.types import (
    CONFIDENCE_MAX,
    CONFIDENCE_MIN,
    AUTO_WRITE_THRESHOLD,
    CONFIRM_THRESHOLD,
    ExtractionResult,
    MemoryCandidate,
)

logger = logging.getLogger("siyi.memory.behavior_extractor")

# ── 值得关注的扩展名 ──

_IMPORTANT_EXTENSIONS: set[str] = {
    ".md", ".txt", ".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go", ".java",
    ".kt", ".swift", ".cpp", ".c", ".h", ".hpp", ".rb", ".php", ".vue", ".svelte",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".csv", ".sql", ".r", ".ipynb", ".pdf",
}

# 临时/缓存/自动生成的目录关键词
_SKIP_DIR_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:^|[/\\])(?:node_modules|\.git|__pycache__|\.venv|venv|\.tox|\.egg-info|\.mypy_cache|\.pytest_cache)(?:$|[/\\])"),
    re.compile(r"(?:^|[/\\])(?:temp|tmp|cache|\.cache|\.temp|\.trash|\.Trash)(?:$|[/\\])"),
    re.compile(r"(?:^|[/\\])\.git/"),
]

_VALUABLE_BROWSER_PATTERNS: list[re.Pattern] = [
    re.compile(r"github\.com|gitlab\.com|bitbucket\.org"),
    re.compile(r"stackoverflow\.com|stackexchange\.com|stackoverflow\.com"),
    re.compile(r"medium\.com|dev\.to|zhihu\.com|zhuanlan\.zhihu\.com"),
    re.compile(r"docs\.\w+\.\w+|learn\.\w+|tutorial|\.md(?:\?|$)"),
    re.compile(r"arxiv\.org|scholar\.google\.com|coursera\.org|udemy\.com"),
    re.compile(r"pypi\.org|crates\.io|npmjs\.com|rubygems\.org"),
    re.compile(r"wikipedia\.org|wiktionary\.org"),
    re.compile(r"news\.ycombinator\.com|hacker-news"),
    re.compile(r"notion\.so|miro\.com|figma\.com|linear\.app"),
    re.compile(r"blog\.|\/blog\/"),
]

# ── 路径分类 ──


def _skip_path(path: str) -> bool:
    """路径是否应跳过（临时文件、缓存、隐藏目录等）。"""
    return any(p.search(path) for p in _SKIP_DIR_PATTERNS)


def _path_depth(path: str) -> int:
    """计算路径深度，用于判断文件重要性。"""
    normalized = path.replace("\\", "/").strip("/")
    return normalized.count("/")


def _classify_file_event(action: str, detail: dict) -> tuple[str, float, list[str]]:
    """将文件操作事件分类为记忆候选。

    Returns:
        (content, confidence, tags) 三元组。content 为空时表示不感兴趣。
    """
    path = detail.get("path", "")
    ext = detail.get("extension", "").lower()

    if not path or _skip_path(path):
        return "", 0.0, []

    filename = path.replace("\\", "/").split("/")[-1]
    depth = _path_depth(path)
    is_dotfile = filename.startswith(".")

    if is_dotfile and ext not in _IMPORTANT_EXTENSIONS:
        return "", 0.0, []

    action_labels = {
        "file_created": "创建了",
        "file_modified": "修改了",
        "file_deleted": "删除了",
        "file_moved": "移动了",
        "file_opened": "打开了",
    }
    label = action_labels.get(action, action)

    # 基础置信度取决于扩展名和路径深度
    if ext in _IMPORTANT_EXTENSIONS:
        if ext in (".md", ".txt", ".pdf") and depth >= 1:
            confidence = 0.62  # 文档类：有价值
        elif ext in (".py", ".js", ".ts", ".rs", ".go", ".java", ".kt", ".swift"):
            confidence = 0.58  # 代码类
        elif ext == ".ipynb":
            confidence = 0.60  # 数据探索
        elif ext == ".csv":
            confidence = 0.50
        else:
            confidence = 0.48
    else:
        return "", 0.0, []

    # 深层路径 → 更可能是用户主动操作的项目文件
    if depth >= 2:
        confidence += 0.05
    if depth >= 4:
        confidence += 0.03

    # 删除操作 → 降低置信度（除非是故意的，但很难判断）
    if action == "file_deleted":
        confidence -= 0.10
        if confidence < CONFIRM_THRESHOLD:
            return "", 0.0, []

    content = f"用户{label}了文件 {path}"
    tags = ["behavior", "file", ext.lstrip("."), filename.lower()]

    return content, min(confidence, CONFIDENCE_MAX), tags


def _classify_browser_event(action: str, detail: dict) -> tuple[str, float, list[str]]:
    """将浏览器事件分类为记忆候选。"""
    url = detail.get("url", "")
    title = detail.get("title", "")

    if not url:
        return "", 0.0, []

    tags = ["behavior", "browser"]

    # 检查搜索查询 — 提取搜索词
    if action in ("search_queried",):
        query = detail.get("query", "") or detail.get("title", "")
        if query:
            content = f"用户搜索了「{query[:80]}」"
            tags.extend(["search", "research"])
            return content, 0.50, tags
        return "", 0.0, []

    # 检查是否有价值的域名
    is_valuable = any(p.search(url) for p in _VALUABLE_BROWSER_PATTERNS)

    # 提取页面主题
    topic = ""
    if title:
        # 去掉站点名后缀
        topic = re.sub(r"\s*[-–—|]\s*[^-–—|]*$", "", title).strip()
        if len(topic) > 100:
            topic = topic[:100]
    if not topic:
        topic = url

    # 构建内容
    if is_valuable:
        if action == "page_visited":
            content = f"用户浏览了{topic or url}"
            tags.append("research")
            return content, 0.58, tags
        elif action == "page_updated":
            return f"用户更新了页面{topic or url}", 0.45, tags
        elif action == "page_summarized":
            return f"用户总结了网页{topic or url}", 0.55, tags
        elif action == "page_explained":
            return f"用户查询了「{title[:60]}」", 0.55, tags
    else:
        # 非学习域名 — 只对明确的阅读/总结动作记
        if action in ("page_summarized", "page_explained", "note_created_from_page"):
            return f"用户从网页创建了笔记：{topic[:80]}", 0.50, tags

    return "", 0.0, []


def _classify_app_event(action: str, detail: dict) -> tuple[str, float, list[str]]:
    """将 App 状态事件分类为记忆候选。"""
    if action == "app_focus":
        return "", 0.0, []  # 单次聚焦不值得记
    if action == "app_shutdown":
        return "", 0.0, []  # 关机事件由系统记录

    return "", 0.0, []


# ── BehaviorMemoryExtractor ──


class BehaviorMemoryExtractor:
    """行为事件记忆提取器。

    从 BehaviorLogger 中拉取行为事件，分类并转换为 MemoryCandidate，
    经过隐私过滤和去重后，写入记忆调度系统。

    用法：
        extractor = BehaviorMemoryExtractor(
            behavior_logger=behavior_logger,
            memory_scheduler=scheduler,
            data_store=data_store,
        )
        result = extractor.extract()
    """

    def __init__(
        self,
        behavior_logger=None,
        memory_scheduler: MemoryScheduler | None = None,
        memory_index_service=None,
        data_store=None,
    ) -> None:
        self._logger = behavior_logger
        self._scheduler = memory_scheduler
        self._memory_index = memory_index_service
        self._data_store = data_store

        # 复用现有组件
        self._privacy_filter = PrivacyFilter()
        self._dedup_engine = DedupEngine()

        # 持久化的检查点：记录上次处理到哪条事件
        self._checkpoint_key = "behavior_memory_checkpoint"
        self._checkpoint: dict[str, Any] = self._load_checkpoint()

        # 配置
        self._config: dict[str, Any] = {
            "file_events": True,
            "browser_events": True,
            "app_events": False,
            "min_confidence": CONFIRM_THRESHOLD,  # 低于 0.5 直接丢弃
            "max_events_per_run": 100,
        }

    # ── 公开 API ──

    def extract(
        self,
        limit: int | None = None,
        existing_memories: list[dict[str, Any]] | None = None,
    ) -> ExtractionResult:
        """执行一次提取：查询行为日志 → 分类 → 过滤 → 去重 → 存储。

        Args:
            limit: 本次最多处理的事件数，默认使用配置值。
            existing_memories: 已有记忆列表（用于去重），默认空。

        Returns:
            ExtractionResult 包含分类后的候选、自动写入、待确认等。
        """
        if self._logger is None:
            return ExtractionResult()
        if not self._config.get("extraction_enabled", True):
            logger.debug("behavior memory extraction is disabled")
            return ExtractionResult()

        effective_limit = limit or self._config["max_events_per_run"]

        # 1. 拉取行为事件
        events = self._query_events(effective_limit)
        if not events:
            logger.debug("no new behavior events to extract")
            return ExtractionResult()

        logger.info("processing %d behavior events for memory extraction", len(events))

        # 2. 转换为候选
        candidates = self._events_to_candidates(events)
        if not candidates:
            self._save_checkpoint(events)
            return ExtractionResult()

        logger.debug("generated %d memory candidates from behavior events", len(candidates))

        # 3. 隐私过滤
        safe_candidates, sensitive = self._privacy_filter.filter_candidates(candidates)
        if sensitive:
            logger.info("privacy filter blocked %d behavior candidates", len(sensitive))

        result = ExtractionResult(candidates=safe_candidates)
        existing = existing_memories or []
        stored = 0

        # 4. 逐条处理
        for c in safe_candidates:
            # 去重
            if existing:
                dedup = self._dedup_engine.check(c, existing)
                if dedup["status"] == "duplicate":
                    result.duplicates.append(c)
                    continue
                if dedup["status"] == "merge":
                    merged = self._dedup_engine.merge(c, dedup["matched_entry"])
                    result.merged.append(merged)
                    # 存储合并后的条目，跳过原始候选
                    if self._store_merged(merged):
                        stored += 1
                    continue

            # 置信度分类
            if c.confidence >= AUTO_WRITE_THRESHOLD:
                result.auto_write.append(c)
            elif c.confidence >= CONFIRM_THRESHOLD:
                result.needs_confirm.append(c)
            else:
                result.discarded.append(c)

        # 5. 写入记忆调度系统
        for c in result.auto_write:
            if self._store_candidate(c):
                stored += 1
        for c in result.needs_confirm:
            if self._store_candidate(c):
                stored += 1

        logger.info(
            "behavior memory extraction: %d candidates, %d stored, %d discarded, %d sensitive",
            len(safe_candidates), stored, len(result.discarded), len(sensitive),
        )

        # 6. 更新检查点
        self._save_checkpoint(events)

        return result

    # ── 事件查询 ──

    def _query_events(self, limit: int) -> list[dict[str, Any]]:
        """从 BehaviorLogger 查询新事件。"""
        since = self._checkpoint.get("last_timestamp")
        logger.debug("querying behavior events since=%s limit=%d", since, limit)
        try:
            events = self._logger.query(
                since=since,
                limit=limit,
            )
        except Exception as exc:
            logger.warning("failed to query behavior events: %s", exc)
            return []
        if not isinstance(events, list):
            return []
        return events

    # ── 候选生成 ──

    def _events_to_candidates(self, events: list[dict[str, Any]]) -> list[MemoryCandidate]:
        """将原始事件列表转换为记忆候选列表。"""
        config = self._config
        candidates: list[MemoryCandidate] = []

        for ev in events:
            category = str(ev.get("category", "") or "")
            action = str(ev.get("action", "") or "")
            detail = ev.get("detail", {})
            if not isinstance(detail, dict):
                detail = {}

            content: str = ""
            confidence: float = 0.0
            tags: list[str] = []

            if category == "file" and config["file_events"]:
                content, confidence, tags = _classify_file_event(action, detail)
            elif category == "browser" and config["browser_events"]:
                content, confidence, tags = _classify_browser_event(action, detail)
            elif category == "app" and config["app_events"]:
                content, confidence, tags = _classify_app_event(action, detail)
            else:
                continue

            if not content or confidence < config["min_confidence"]:
                continue

            # 归类判断
            mem_type = self._infer_memory_type(category, action, detail)
            reason = self._infer_reason(category, action, detail)

            candidates.append(
                MemoryCandidate(
                    type=mem_type,
                    content=content,
                    confidence=min(CONFIDENCE_MAX, max(CONFIDENCE_MIN, confidence)),
                    source="user",
                    tags=tags,
                    reason=reason,
                )
            )

        return candidates

    @staticmethod
    def _infer_memory_type(category: str, action: str, detail: dict) -> str:
        """推断行为事件的记忆类型。"""
        if category == "file":
            ext = detail.get("extension", "").lower()
            if ext in (".md", ".txt", ".pdf"):
                return "factual"
            elif ext in (".py", ".js", ".ts", ".rs", ".go", ".java", ".kt"):
                return "project"
            else:
                return "factual"
        elif category == "browser":
            if action in ("page_summarized", "page_explained", "note_created_from_page"):
                return "factual"
            return "factual"
        return "factual"

    @staticmethod
    def _infer_reason(category: str, action: str, detail: dict) -> str:
        """生成记忆理由（供用户理解为什么要记这条）。"""
        if category == "file":
            path = detail.get("path", "")
            return f"用户操作了文件 {path}"
        elif category == "browser":
            url = detail.get("url", "")
            title = detail.get("title", "")
            source = title or url
            return f"用户浏览了{source[:80]}"
        return "行为日志提取"

    # ── 持久化存储 ──

    def _store_candidate(self, candidate: MemoryCandidate) -> bool:
        """将候选写入记忆调度系统。"""
        if self._scheduler is None:
            return False
        try:
            memory = self._scheduler.add_memory(
                memory_id=f"behavior_{candidate.type}_{uuid.uuid4().hex[:12]}",
                content=candidate.content,
                memory_type=candidate.type,
                tags=candidate.tags,
            )
            return memory is not None
        except Exception as exc:
            logger.warning("failed to store behavior memory: %s", exc)
            return False

    def _store_merged(self, merged: dict[str, Any]) -> bool:
        """存储合并后的记忆条目。"""
        if self._scheduler is None:
            return False
        try:
            content = str(merged.get("content", ""))
            mem_type = str(merged.get("type", "factual"))
            tags = list(merged.get("tags", []))
            memory = self._scheduler.add_memory(
                memory_id=f"behavior_merged_{uuid.uuid4().hex[:12]}",
                content=content,
                memory_type=mem_type,
                tags=tags,
            )
            return memory is not None
        except Exception as exc:
            logger.warning("failed to store merged behavior memory: %s", exc)
            return False

    # ── 检查点管理 ──

    def _load_checkpoint(self) -> dict[str, Any]:
        if self._data_store is None:
            return {}
        try:
            val = self._data_store.load_json(self._checkpoint_key, {})
            return dict(val) if isinstance(val, dict) else {}
        except Exception:
            return {}

    def _save_checkpoint(self, events: list[dict[str, Any]]) -> None:
        if self._data_store is None or not events:
            return
        last_ts = events[-1].get("timestamp", "") if events else ""
        if not last_ts:
            return
        self._checkpoint["last_timestamp"] = last_ts
        self._checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            self._data_store.save_json(self._checkpoint_key, self._checkpoint)
        except Exception as exc:
            logger.warning("failed to save behavior memory checkpoint: %s", exc)

    # ── 配置 ──

    @property
    def config(self) -> dict[str, Any]:
        return dict(self._config)

    def update_config(self, **kwargs: Any) -> None:
        """更新配置。"""
        for key, value in kwargs.items():
            if key in self._config:
                self._config[key] = value

    def set_extraction_enabled(self, enabled: bool) -> None:
        """启用/禁用提取。"""
        old = self._config.get("extraction_enabled", True)
        self._config["extraction_enabled"] = enabled
        if old != enabled:
            logger.info("behavior memory extraction enabled: %s", enabled)

    def get_status(self) -> dict[str, Any]:
        """获取当前状态。"""
        checkpoint = self._checkpoint or {}
        return {
            "enabled": self._config.get("extraction_enabled", True),
            "last_checked_at": checkpoint.get("last_timestamp", ""),
            "config": dict(self._config),
        }
