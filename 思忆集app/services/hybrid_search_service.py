# -*- coding: utf-8 -*-
"""
HybridSearchService — 统一混合搜索服务

融合 BM25 关键词检索 + 向量检索 + 重排序，作为记忆和知识库检索的统一入口。

设计要点：
- 可运行时调整的 BM25/向量权重 (alpha)
- 重排序支持 RRF（默认）和交叉编码器（可选）
- EventBus 集成：搜索完成时 emit "search.completed" 事件
- 优雅降级：向量服务不可用时自动回退到纯关键词检索
- 线程安全，可注册到 ServiceRegistry
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from memory.search._engine import HybridMemorySearch

logger = logging.getLogger("siyi.hybrid_search_service")

try:
    from core.events import event_bus, AppEvent

    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False


class ReRankMethod(str, Enum):
    """重排序方法枚举。"""

    RRF = "rrf"  # Reciprocal Rank Fusion
    CROSS_ENCODER = "cross_encoder"  # 交叉编码器（需额外模型）
    NONE = "none"  # 不重排序，直接融合


class SearchMode(str, Enum):
    """搜索模式枚举。"""

    HYBRID = "hybrid"  # 混合搜索（向量 + BM25）
    VECTOR = "vector"  # 仅向量检索
    KEYWORD = "keyword"  # 仅关键词检索


@dataclass
class SearchConfig:
    """运行时搜索配置，支持动态调整权重。

    Attributes:
        alpha: 向量分数权重 (0~1)，1=纯向量，0=纯 BM25
        rrf_k: RRF 常数，默认 60
        top_k: 默认最多返回结果数
        re_rank_method: 重排序方法
        enable_event: 是否在搜索完成时发射事件
        vector_top_k_ratio: 向量检索取回倍数（相对于 top_k）
        keyword_top_k_ratio: 关键词检索取回倍数（相对于 top_k）
    """

    alpha: float = 0.7
    rrf_k: int = 60
    top_k: int = 10
    re_rank_method: ReRankMethod = ReRankMethod.RRF
    enable_event: bool = True
    vector_top_k_ratio: float = 2.0
    keyword_top_k_ratio: float = 2.0

    def __post_init__(self) -> None:
        self.alpha = max(0.0, min(1.0, self.alpha))
        self.rrf_k = max(1, self.rrf_k)
        self.top_k = max(1, self.top_k)


class HybridSearchService:
    """统一混合搜索服务。

    封装底层搜索引擎，提供可运行时配置的混合搜索能力。

    用法:
        service = HybridSearchService(memory_search=hybrid_memory_search)
        result = service.search("关于数据库的记忆")
        service.update_config(alpha=0.5)  # 运行时调权重
    """

    def __init__(
        self,
        memory_search: HybridMemorySearch | None = None,
        config: SearchConfig | None = None,
    ) -> None:
        """
        Args:
            memory_search: HybridMemorySearch 实例（可选）。
            config: 初始搜索配置，为 None 时使用默认值。
        """
        self._memory_search = memory_search
        self._config = config or SearchConfig()
        self._lock = Lock()
        self._pre_search_hooks: list[Callable[[str, SearchConfig], str]] = []
        self._post_search_hooks: list[Callable[[dict[str, Any]], None]] = []

    # ── 配置管理 ──────────────────────────────────────────────────────

    @property
    def config(self) -> SearchConfig:
        """获取当前搜索配置（线程安全）。"""
        with self._lock:
            return SearchConfig(
                alpha=self._config.alpha,
                rrf_k=self._config.rrf_k,
                top_k=self._config.top_k,
                re_rank_method=self._config.re_rank_method,
                enable_event=self._config.enable_event,
                vector_top_k_ratio=self._config.vector_top_k_ratio,
                keyword_top_k_ratio=self._config.keyword_top_k_ratio,
            )

    def update_config(self, **kwargs: Any) -> SearchConfig:
        """运行时更新搜索配置。

        Args:
            **kwargs: 要更新的配置字段（如 alpha=0.5, rrf_k=30）。

        Returns:
            更新后的配置副本。
        """
        allowed = {
            "alpha",
            "rrf_k",
            "top_k",
            "re_rank_method",
            "enable_event",
            "vector_top_k_ratio",
            "keyword_top_k_ratio",
        }
        with self._lock:
            for key, value in kwargs.items():
                if key not in allowed:
                    logger.warning("Ignoring unknown config key: %s", key)
                    continue
                setattr(self._config, key, value)
            self._config.__post_init__()
            cfg = SearchConfig(
                alpha=self._config.alpha,
                rrf_k=self._config.rrf_k,
                top_k=self._config.top_k,
                re_rank_method=self._config.re_rank_method,
                enable_event=self._config.enable_event,
                vector_top_k_ratio=self._config.vector_top_k_ratio,
                keyword_top_k_ratio=self._config.keyword_top_k_ratio,
            )
        logger.info("Search config updated: alpha=%.2f, rrf_k=%d, top_k=%d", cfg.alpha, cfg.rrf_k, cfg.top_k)
        return cfg

    # ── 钩子管理 ──────────────────────────────────────────────────────

    def add_pre_search_hook(self, hook: Callable[[str, SearchConfig], str]) -> None:
        """添加搜索前钩子：接收原始查询和配置，返回（可能修改后的）查询。"""
        with self._lock:
            self._pre_search_hooks.append(hook)

    def add_post_search_hook(self, hook: Callable[[dict[str, Any]], None]) -> None:
        """添加搜索后钩子：接收搜索结果字典，可进行后处理。"""
        with self._lock:
            self._post_search_hooks.append(hook)

    # ── 核心搜索接口 ──────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int | None = None,
        search_mode: str = "hybrid",
        source_filters: list[str] | None = None,
        time_range: tuple[datetime | None, datetime | None] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """执行混合搜索。

        Args:
            query: 用户查询文本。
            top_k: 最多返回结果数，为 None 时使用配置值。
            search_mode: 搜索模式 ("hybrid" | "vector" | "keyword")。
            source_filters: 来源类型过滤列表。
            time_range: (start, end) 时间范围元组。
            **kwargs: 传递给底层搜索引擎的额外参数。

        Returns:
            搜索结果字典，包含 items/count/mode/elapsed_ms 等字段。

        Raises:
            RuntimeError: 当没有可用的搜索引擎时。
        """
        start_time = time.monotonic()
        config = self.config
        effective_top_k = top_k if top_k is not None else config.top_k
        normalized_mode = search_mode.strip().lower()
        if normalized_mode not in ("hybrid", "vector", "keyword"):
            normalized_mode = "hybrid"

        # 1. 执行搜索前钩子
        effective_query = query
        for hook in self._pre_search_hooks:
            try:
                effective_query = hook(effective_query, config)
            except Exception as exc:
                logger.warning("pre_search_hook failed: %s", exc, exc_info=True)

        # 2. 执行搜索（带优雅降级）
        result = self._execute_search(
            query=effective_query,
            top_k=effective_top_k,
            search_mode=search_mode,
            source_filters=source_filters,
            time_range=time_range,
            config=config,
            **kwargs,
        )

        # 3. 计算耗时
        elapsed_ms = (time.monotonic() - start_time) * 1000
        result["elapsed_ms"] = round(elapsed_ms, 2)

        # 4. 发射 EventBus 事件
        if config.enable_event and HAS_EVENT_BUS:
            try:
                event_bus.emit(
                    AppEvent(
                        event_type="search.completed",
                        payload={
                            "query": query,
                            "effective_query": effective_query,
                            "mode": normalized_mode,
                            "count": result.get("count", 0),
                            "elapsed_ms": result["elapsed_ms"],
                            "source_filters": source_filters,
                        },
                        source="hybrid_search_service",
                    )
                )
            except Exception as exc:
                logger.warning("EventBus emit failed: %s", exc, exc_info=True)

        # 5. 执行搜索后钩子
        for hook in self._post_search_hooks:
            try:
                hook(result)
            except Exception as exc:
                logger.warning("post_search_hook failed: %s", exc, exc_info=True)

        return result

    # ── 记忆浮现接口（与 HybridMemorySearch.find_related 契约对齐）────

    def find_related(
        self,
        text: str,
        *,
        limit: int = 5,
        min_score: float = 0.3,
    ) -> list[dict[str, Any]]:
        """根据文本内容查找相关记忆（记忆浮现用，前端侧栏非模态展示）。

        契约对齐 memory/search/_engine.py::HybridMemorySearch.find_related：
        返回 list[dict]，每项含 id / title / summary / source_type /
        source_label / score / created_at。

        背景（P0 案 H）：装配时 AssistantMemoryIndexService._memory_search 被
        塞进本类实例，而本类此前没有 find_related → AttributeError 被上层吞成
        -32603。补契约的正解在此：内部委托本类 search()（默认 hybrid 模式，
        向量不可用时其 _execute_search 已自动降级 keyword）。

        分数语义：向量分（vector_score，相似度 0~1）优先直接可比；纯关键词
        结果的 bm25_score 量纲无上界，按组内最大值归一化映射到 [0,1] 后再与
        min_score 比较，保持与原向量实现相近的过滤语义。

        Args:
            text: 当前文本内容。
            limit: 最多返回条数。
            min_score: 最低相关性分数阈值（归一化后 0~1）。

        Returns:
            精简相关记忆列表（契约同 HybridMemorySearch.find_related）。
        """
        if not text or not text.strip():
            return []
        limit = max(1, int(limit))
        result = self.search(query=text, top_k=max(limit * 2, 10))
        items = list(result.get("items") or [])

        bm25_values = [float(item["bm25_score"]) for item in items if isinstance(item.get("bm25_score"), (int, float))]
        bm25_max = max(bm25_values, default=0.0)

        seen: set[str] = set()
        related: list[dict[str, Any]] = []
        for item in items:
            item_id = str(item.get("entry_id") or item.get("id") or "")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)

            score = self._related_score(item, bm25_max=bm25_max)
            if score < min_score:
                continue

            content = str(item.get("content") or item.get("text") or "")
            related.append(
                {
                    "id": item_id,
                    "title": str(item.get("title") or "") or content[:60],
                    "summary": content[:200],
                    "source_type": str(item.get("source_type") or ""),
                    "source_label": self._related_source_label(item),
                    "score": round(score, 3),
                    "created_at": str(item.get("created_at") or item.get("updated_at") or ""),
                }
            )
            if len(related) >= limit:
                break
        return related

    @staticmethod
    def _related_score(item: dict[str, Any], *, bm25_max: float) -> float:
        """从搜索结果条目提取归一化相关性分数（0~1 语义）。"""
        vector_score = item.get("vector_score")
        if isinstance(vector_score, (int, float)) and float(vector_score) > 0:
            return float(vector_score)
        score = item.get("score")
        if isinstance(score, (int, float)) and float(score) > 0:
            return float(score)
        bm25 = item.get("bm25_score")
        if isinstance(bm25, (int, float)) and float(bm25) > 0 and bm25_max > 0:
            return min(1.0, float(bm25) / bm25_max)
        return 0.0

    @staticmethod
    def _related_source_label(item: dict[str, Any]) -> str:
        """来源标签：优先用 search() 已富化的 source_label，缺失时本地兜底。"""
        label = str(item.get("source_label") or "").strip()
        if label:
            return label
        source_type = str(item.get("source_type") or "")
        try:
            from memory.search._parsing import get_source_label

            return get_source_label(source_type)
        except Exception:
            return f"[{source_type}]" if source_type else "[记忆]"

    # ── 索引管理 ──────────────────────────────────────────────────────

    def sync_entries(self, entries: list[dict[str, Any]]) -> int:
        """全量同步记忆条目到索引。

        Args:
            entries: 记忆条目列表。

        Returns:
            同步的条目数。
        """
        if self._memory_search is not None:
            return self._memory_search.sync_entries(entries)
        return 0

    def add_entry(self, entry: dict[str, Any]) -> None:
        """添加单条记忆条目到索引。"""
        if self._memory_search is not None:
            self._memory_search.add_entry(entry)

    def update_entry(self, entry_id: str, entry: dict[str, Any]) -> None:
        """更新索引中的记忆条目。"""
        if self._memory_search is not None:
            self._memory_search.update_entry(entry_id, entry)

    def delete_entry(self, entry_id: str) -> None:
        """从索引中删除记忆条目。"""
        if self._memory_search is not None:
            self._memory_search.delete_entry(entry_id)

    def get_entry_count(self) -> int:
        """返回索引中的条目总数。"""
        if self._memory_search is not None:
            return self._memory_search.get_entry_count()
        return 0

    def close(self) -> None:
        """释放资源。"""
        if self._memory_search is not None:
            self._memory_search.close()

    # ── 内部实现 ──────────────────────────────────────────────────────

    def _execute_search(
        self,
        query: str,
        top_k: int,
        search_mode: str,
        source_filters: list[str] | None,
        time_range: tuple[datetime | None, datetime | None] | None,
        config: SearchConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """执行实际搜索，带优雅降级逻辑。"""
        if self._memory_search is None:
            return {
                "query": query,
                "effective_query": query,
                "mode": "none",
                "count": 0,
                "items": [],
                "message": "No search engine available",
            }

        # 标准化搜索模式
        normalized_mode = search_mode.strip().lower()
        if normalized_mode not in ("hybrid", "vector", "keyword"):
            normalized_mode = "hybrid"

        try:
            if normalized_mode == "vector":
                result = self._memory_search.search(
                    query=query,
                    top_k=top_k,
                    search_mode="vector",
                    source_filters=source_filters,
                    time_range=time_range,
                    **kwargs,
                )
            elif normalized_mode == "keyword":
                result = self._memory_search.search(
                    query=query,
                    top_k=top_k,
                    search_mode="keyword",
                    source_filters=source_filters,
                    time_range=time_range,
                    **kwargs,
                )
            else:
                # 混合搜索模式
                result = self._memory_search.search(
                    query=query,
                    top_k=top_k,
                    search_mode="hybrid",
                    source_filters=source_filters,
                    time_range=time_range,
                    **kwargs,
                )

                # 应用可选的配置权重（alpha 调整）
                if config.alpha != 0.7 and result.get("items"):
                    result = self._apply_alpha_weight(result, config.alpha)

                # 应用重排序
                if config.re_rank_method == ReRankMethod.RRF:
                    result = self._ensure_rrf(result)
                elif config.re_rank_method == ReRankMethod.CROSS_ENCODER:
                    result = self._cross_encoder_re_rank(result, query)

        except Exception as exc:
            logger.warning(
                "Hybrid search failed (%s), falling back to keyword search",
                exc,
            )
            # 优雅降级：回退到关键词检索
            result = self._fallback_search(query, top_k, source_filters, time_range, **kwargs)

        return result

    def _apply_alpha_weight(
        self,
        result: dict[str, Any],
        alpha: float,
    ) -> dict[str, Any]:
        """应用 alpha 权重调整融合分数。

        Args:
            result: 原始搜索结果。
            alpha: 向量权重 (0~1)。

        Returns:
            调整后的搜索结果。
        """
        items = list(result.get("items") or [])
        if not items:
            return result

        for item in items:
            vector_score = item.get("vector_score") or item.get("vector_similarity", 0.0)
            bm25_score = item.get("bm25_score", 0.0)
            if isinstance(vector_score, (int, float)) and isinstance(bm25_score, (int, float)):
                item["_fusion_score"] = vector_score * alpha + bm25_score * (1.0 - alpha)

        # 按融合分数重新排序
        items.sort(key=lambda x: x.get("_fusion_score", 0.0), reverse=True)
        result["items"] = items
        result["count"] = len(items)
        return result

    def _ensure_rrf(self, result: dict[str, Any]) -> dict[str, Any]:
        """确保 RRF 混合排序已应用（底层 HybridMemorySearch 已内置 RRF）。"""
        # HybridMemorySearch._hybrid_search 已经做了 RRF 融合
        # 这里确保 items 按分数合理排序
        items = list(result.get("items") or [])
        if not items:
            return result
        # 确保有排序键
        if items and all(item.get("bm25_score") is not None for item in items):
            items.sort(key=lambda x: x.get("bm25_score", 0.0), reverse=True)
            result["items"] = items
        return result

    def _cross_encoder_re_rank(
        self,
        result: dict[str, Any],
        query: str,
    ) -> dict[str, Any]:
        """使用交叉编码器对结果进行重排序（占位实现）。

        Args:
            result: 搜索结果。
            query: 原始查询。

        Returns:
            重排序后的结果。

        Note:
            交叉编码器需要加载额外的模型（如 BAAI/bge-reranker-v2-m3），
            当前为占位实现，直接返回原始结果。实际使用时需要：
            1. 在首次调用时 lazy-load 模型
            2. 对 (query, candidate_text) pair 计算相关性分数
            3. 按分数重新排序
        """
        logger.debug("Cross-encoder re-ranking not yet implemented, using default ordering")
        return result

    def _fallback_search(
        self,
        query: str,
        top_k: int,
        source_filters: list[str] | None,
        time_range: tuple[datetime | None, datetime | None] | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """当混合搜索失败时的降级搜索。

        Args:
            query: 查询文本。
            top_k: 最多返回结果数。
            source_filters: 来源过滤。
            time_range: 时间范围。
            **kwargs: 额外参数。

        Returns:
            降级后的搜索结果。
        """
        if self._memory_search is None:
            return {
                "query": query,
                "effective_query": query,
                "mode": "none",
                "count": 0,
                "items": [],
                "message": "No search engine available",
                "degraded": True,
            }

        try:
            result = self._memory_search.search(
                query=query,
                top_k=top_k,
                search_mode="keyword",
                source_filters=source_filters,
                time_range=time_range,
                **kwargs,
            )
            result["degraded"] = True
            result["degraded_reason"] = "vector_search_failed"
            logger.info("Search degraded to keyword-only mode for query: %s", query[:60])
            return result
        except Exception as exc:
            logger.error("Fallback search also failed: %s", exc)
            return {
                "query": query,
                "effective_query": query,
                "mode": "none",
                "count": 0,
                "items": [],
                "message": f"Search unavailable: {exc}",
                "degraded": True,
                "degraded_reason": "all_engines_failed",
            }

    # ── 运行时状态 ────────────────────────────────────────────────────

    def health_check(self) -> dict[str, Any]:
        """返回服务健康状态。

        Returns:
            包含搜索引擎状态和配置的字典。
        """
        config = self.config
        engine_type = "none"
        if self._memory_search is not None:
            engine_type = type(self._memory_search).__name__
        return {
            "available": self._memory_search is not None,
            "engine_type": engine_type,
            "config": {
                "alpha": config.alpha,
                "rrf_k": config.rrf_k,
                "top_k": config.top_k,
                "re_rank_method": config.re_rank_method.value,
                "enable_event": config.enable_event,
            },
            "entry_count": self.get_entry_count(),
        }
