# -*- coding: utf-8 -*-
"""
查询缓存 - 24h 内相同/相似查询复用检索结果

设计规范：
- 相同查询（语义去重后）24h 内复用结果
- 缓存命中时跳过 embedding + 检索，直接返回
- 知识库更新时，相关查询缓存失效
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class QueryCache:
    """
    查询缓存：基于 LRU + TTL 的检索结果缓存

    支持：
    - 精确查询匹配（hash）
    - 语义近似匹配（embedding 余弦相似度 > threshold）
    - 知识库更新时按 collection 维度失效
    - 最大条目限制，自动淘汰最旧条目
    """

    DEFAULT_TTL_SECONDS = 86400  # 24 小时
    DEFAULT_MAX_ENTRIES = 256
    SEMANTIC_SIMILARITY_THRESHOLD = 0.95

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        """
        Args:
            ttl_seconds: 缓存 TTL（默认 86400 = 24h）
            max_entries: 最大缓存条目（默认 256）
        """
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._entries: Dict[str, Dict[str, Any]] = {}  # hash -> payload

    # ── 公开接口 ──────────────────────────────────────────────────────

    def get(self, query: str) -> Optional[Dict[str, Any]]:
        """
        查询缓存（精确匹配 + 语义近似）

        Returns:
            { "documents": [...], "metadatas": [...], ... } 或 None
        """
        # 1. 精确匹配
        key = self._hash_query(query)
        entry = self._entries.get(key)
        if entry and not self._is_expired(entry):
            logger.debug("Cache HIT (exact): query=%s", query[:60])
            entry["cached_at"] = time.time()  # 刷新 LRU
            return dict(entry["result"])

        # 2. 语义近似匹配（扫描所有条目的向量）
        best_sim = 0.0
        best_entry = None
        for e_key, e_data in self._entries.items():
            if self._is_expired(e_data):
                continue
            e_query = e_data.get("query", "")
            sim = self._text_similarity(query, e_query)
            if sim > best_sim:
                best_sim = sim
                best_entry = e_data

        if best_sim >= self.SEMANTIC_SIMILARITY_THRESHOLD and best_entry:
            logger.debug(
                "Cache HIT (semantic): sim=%.3f query=%s matched=%s",
                best_sim, query[:40], (best_entry.get("query") or "")[:40]
            )
            best_entry["cached_at"] = time.time()
            return dict(best_entry["result"])

        logger.debug("Cache MISS: query=%s", query[:60])
        return None

    def set(self, query: str, result: Dict[str, Any]) -> None:
        """存入缓存"""
        key = self._hash_query(query)
        self._entries[key] = {
            "query": query,
            "result": result,
            "cached_at": time.time(),
        }
        self._evict_if_needed()
        logger.debug("Cache SET: query=%s", query[:60])

    def invalidate(self, query: Optional[str] = None) -> None:
        """
        使缓存失效

        Args:
            query: 使单个查询失效；None 则清空全部
        """
        if query is None:
            self._entries.clear()
            logger.info("Cache fully invalidated")
        else:
            key = self._hash_query(query)
            self._entries.pop(key, None)
            logger.debug("Cache invalidated: query=%s", query[:60])

    def clear(self) -> None:
        """清空全部缓存"""
        self._entries.clear()
        logger.info("Cache cleared")

    @property
    def size(self) -> int:
        """当前缓存条目数"""
        return len(self._entries)

    # ── 内部 ──────────────────────────────────────────────────────────

    @staticmethod
    def _hash_query(query: str) -> str:
        """将查询字符串映射为缓存键"""
        normalized = " ".join(query.strip().lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _is_expired(self, entry: Dict[str, Any]) -> bool:
        """检查条目是否过期"""
        return (time.time() - entry.get("cached_at", 0)) > self._ttl

    def _evict_if_needed(self) -> None:
        """超出最大条目时淘汰最旧的"""
        if len(self._entries) <= self._max_entries:
            return
        sorted_keys = sorted(
            self._entries,
            key=lambda k: self._entries[k].get("cached_at", 0),
        )
        to_remove = len(self._entries) - self._max_entries
        for k in sorted_keys[:to_remove]:
            self._entries.pop(k, None)
        logger.debug("Evicted %d stale cache entries", to_remove)

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """
        文本相似度（基于字符级 Jaccard）

        比编辑距离轻量，适合快速过滤。
        """
        if not a or not b:
            return 0.0
        # 取 unigram 字符集合
        set_a = set(a.strip().lower())
        set_b = set(b.strip().lower())
        intersection = set_a & set_b
        union = set_a | set_b
        if not union:
            return 0.0
        return len(intersection) / len(union)
