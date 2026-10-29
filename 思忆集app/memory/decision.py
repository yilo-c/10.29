# -*- coding: utf-8 -*-
"""Decision memory — historical decision storage with similarity matching.

Stores past assistant decisions with their context and outcomes,
enables similarity-based retrieval to inform future decisions.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.memory.decision")

# 用于英文/数字分词；CJK 字符单独切出
_WORD_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf]")


def _tokenize(text: str) -> set[str]:
    """混合分词：拉丁/数字按词，CJK 按字符。

    避免对中文使用单纯 ``text.split()`` 退化成整段一个 token 的问题。
    """
    if not text:
        return set()
    text = text.lower()
    tokens: set[str] = set(_WORD_TOKEN_RE.findall(text))
    tokens.update(_CJK_RE.findall(text))
    return tokens


def _char_bigrams(text: str) -> set[str]:
    """生成字符级 bigram，用于跨语言相似度（尤其 CJK）。"""
    if not text:
        return set()
    compact = re.sub(r"\s+", "", text.lower())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union) if union else 0.0


@dataclass
class DecisionRecord:
    """单个决策记录。"""
    decision_id: str
    goal: str                            # 决策目标
    context: str                         # 决策时的上下文
    chosen_path: str                     # 选择的方案
    alternatives: list[str]              # 备选方案
    reasoning: str                       # 选择理由
    outcome: str = ""                    # 结果反馈（后续更新）
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.8
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "goal": self.goal,
            "context": self.context,
            "chosen_path": self.chosen_path,
            "alternatives": list(self.alternatives),
            "reasoning": self.reasoning,
            "outcome": self.outcome,
            "tags": list(self.tags),
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionRecord:
        return cls(
            decision_id=str(data.get("decision_id", "")),
            goal=str(data.get("goal", "")),
            context=str(data.get("context", "")),
            chosen_path=str(data.get("chosen_path", "")),
            alternatives=[str(a) for a in (data.get("alternatives") or [])],
            reasoning=str(data.get("reasoning", "")),
            outcome=str(data.get("outcome", "")),
            tags=[str(t) for t in (data.get("tags") or [])],
            confidence=float(data.get("confidence", 0.8)),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass
class SimilarityMatch:
    """相似决策匹配结果。"""
    record: DecisionRecord
    similarity: float                     # 0-1 相似度
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.record.to_dict(),
            "similarity": self.similarity,
            "matched_keywords": list(self.matched_keywords),
        }


class DecisionMemory:
    """决策记忆——存储历史决策并支持相似度匹配。

    用于在新决策时参考历史相似场景，避免重复犯错。

    Usage:
        memory = DecisionMemory()
        memory.store(record)
        matches = memory.find_similar(goal="如何优化数据库查询")
    """

    def __init__(self, data_store=None):
        self._data_store = data_store
        self._records: list[DecisionRecord] = []

    # ── Store ────────────────────────────────────────────────────────

    def store(self, record: DecisionRecord) -> str:
        """存储一条决策记录。

        Args:
            record: 决策记录。

        Returns:
            决策 ID。
        """
        self._records.append(record)
        self._persist()
        return record.decision_id

    def store_from_dict(self, data: dict[str, Any]) -> str:
        """从字典创建并存储决策记录。"""
        record = DecisionRecord.from_dict(data)
        return self.store(record)

    # ── Retrieval ────────────────────────────────────────────────────

    def find_similar(
        self,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.3,
    ) -> list[SimilarityMatch]:
        """查找与查询相似的过往决策。

        Args:
            query: 查询文本（决策目标或上下文描述）。
            top_k: 返回的最大匹配数。
            min_similarity: 最低相似度阈值。

        Returns:
            按相似度降序排列的匹配列表。
        """
        if not query or not self._records:
            return []

        query_lower = query.lower()
        query_tokens = _tokenize(query_lower)
        query_bigrams = _char_bigrams(query_lower)

        scored: list[tuple[float, DecisionRecord, list[str]]] = []

        for record in self._records:
            similarity, keywords = self._compute_similarity(
                record, query_lower, query_tokens, query_bigrams
            )
            if similarity >= min_similarity:
                scored.append((similarity, record, keywords))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        return [
            SimilarityMatch(record=record, similarity=round(sim, 4), matched_keywords=keywords)
            for sim, record, keywords in top
        ]

    def get_by_id(self, decision_id: str) -> DecisionRecord | None:
        """根据 ID 获取决策记录。"""
        for record in self._records:
            if record.decision_id == decision_id:
                return record
        return None

    def get_all(self, limit: int = 100) -> list[DecisionRecord]:
        """获取最近的所有决策记录。"""
        return list(self._records[-limit:])

    def update_outcome(self, decision_id: str, outcome: str) -> bool:
        """更新决策的结果反馈。"""
        for record in self._records:
            if record.decision_id == decision_id:
                record.outcome = outcome
                record.updated_at = datetime.now(timezone.utc).isoformat()
                self._persist()
                return True
        return False

    # ── Similarity computation ───────────────────────────────────────

    def _compute_similarity(
        self,
        record: DecisionRecord,
        query_lower: str,
        query_tokens: set[str],
        query_bigrams: set[str],
    ) -> tuple[float, list[str]]:
        """计算记录与查询的相似度。

        混合方法：
          - token Jaccard（拉丁词 + CJK 单字），权重 0.55
          - 字符 bigram Jaccard，权重 0.45（兼顾中文短语相似度）
          - 整句包含 / tag 命中作为 boost
        """
        if not query_tokens and not query_bigrams:
            return 0.0, []

        # Combine searchable text fields
        searchable = " ".join([
            record.goal,
            record.context,
            record.reasoning,
            record.chosen_path,
            " ".join(record.alternatives),
            " ".join(record.tags),
        ]).lower()

        record_tokens = _tokenize(searchable)
        record_bigrams = _char_bigrams(searchable)
        if not record_tokens and not record_bigrams:
            return 0.0, []

        token_jaccard = _jaccard(query_tokens, record_tokens)
        bigram_jaccard = _jaccard(query_bigrams, record_bigrams)
        base = 0.55 * token_jaccard + 0.45 * bigram_jaccard

        # Exact phrase match boost
        phrase_boost = 0.0
        if query_lower and query_lower in record.goal.lower():
            phrase_boost = 0.2
        elif query_lower and query_lower in record.context.lower():
            phrase_boost = 0.1

        # Tag match boost
        tag_boost = 0.0
        for tag in record.tags:
            tag_l = tag.lower()
            if not tag_l:
                continue
            if tag_l in query_lower or query_lower in tag_l:
                tag_boost = 0.15
                break

        similarity = min(1.0, base + phrase_boost + tag_boost)
        matched_keywords = sorted(query_tokens & record_tokens)

        return similarity, matched_keywords

    # ── Persistence ──────────────────────────────────────────────────

    def load(self) -> int:
        """从 data_store 加载历史决策记录。

        Returns:
            加载的记录数。
        """
        if self._data_store is None:
            return 0
        try:
            items = self._data_store.load_json("assistant_decision_memory_history", [])
            if not isinstance(items, list):
                return 0
            self._records = [
                DecisionRecord.from_dict(item)
                for item in items
                if isinstance(item, dict)
            ]
            return len(self._records)
        except Exception as exc:
            logger.warning("decision memory load failed: %s", exc, exc_info=True)
            return 0

    def _persist(self) -> None:
        """持久化决策记录到 data_store。"""
        if self._data_store is None:
            return
        try:
            self._data_store.save_json(
                "assistant_decision_memory_history",
                [r.to_dict() for r in self._records],
            )
        except Exception as exc:
            logger.warning("decision memory persist failed: %s", exc, exc_info=True)

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """获取决策记忆统计。"""
        if not self._records:
            return {"total": 0, "with_outcome": 0}
        with_outcome = sum(1 for r in self._records if r.outcome)
        # Tag frequency
        tags: dict[str, int] = {}
        for r in self._records:
            for t in r.tags:
                tags[t] = tags.get(t, 0) + 1
        top_tags = sorted(tags.items(), key=lambda x: x[1], reverse=True)[:10]

        return {
            "total": len(self._records),
            "with_outcome": with_outcome,
            "top_tags": dict(top_tags),
            "last_updated": self._records[-1].created_at if self._records else "",
        }
