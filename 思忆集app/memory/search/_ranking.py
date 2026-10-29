"""RRF (Reciprocal Rank Fusion) ranking for hybrid search results.

Extracted from memory/search.py — _rrf_fuse method.

支持两种融合模式：
1. 纯 RRF（默认）：仅用 rank，score = sum(1/(k+rank))
2. 加权 RRF + 归一化分数：当 item 含 bm25_score / vector_score 字段时，
   先做 min-max 归一化到 [0,1]，再与 RRF 分数加权融合，
   避免不同量纲的分数直接相加导致偏差。
"""
from __future__ import annotations

from typing import Any


class RankingMixin:
    """Mixin providing RRF fusion for hybrid search.

    Expects no specific instance attributes — all inputs are passed as parameters.
    """

    @staticmethod
    def _rrf_fuse(
        vector_items: list[dict[str, Any]],
        keyword_items: list[dict[str, Any]],
        k: int = 60,
        top_k: int = 10,
        *,
        score_weight: float = 0.3,
    ) -> list[dict[str, Any]]:
        """Reciprocal Rank Fusion (RRF) 融合排序。

        score(item) = (1 - score_weight) * rrf_score + score_weight * normalized_score

        其中：
        - rrf_score = sum(1 / (k + rank(item)))，归一化到 [0,1]
        - normalized_score = bm25_score 和 vector_score 的 min-max 归一化平均值

        Args:
            vector_items: 向量搜索结果列表（可能含 vector_score 字段）
            keyword_items: 关键词搜索结果列表（可能含 bm25_score 字段）
            k: RRF 常数，默认 60
            top_k: 最多返回结果数
            score_weight: 归一化分数的权重（0=纯 RRF，1=纯分数），默认 0.3
        """
        id_ranks: dict[str, dict[str, Any]] = {}

        for rank, item in enumerate(vector_items, start=1):
            item_id = item.get("id") or str(id(item))
            id_ranks[item_id] = {
                "item": item,
                "vector_rank": rank,
                "keyword_rank": None,
                "vector_score": item.get("vector_score") or item.get("score"),
                "bm25_score": item.get("bm25_score"),
            }

        for rank, item in enumerate(keyword_items, start=1):
            item_id = item.get("id") or str(id(item))
            if item_id in id_ranks:
                id_ranks[item_id]["keyword_rank"] = rank
                if id_ranks[item_id]["bm25_score"] is None:
                    id_ranks[item_id]["bm25_score"] = item.get("bm25_score")
            else:
                id_ranks[item_id] = {
                    "item": item,
                    "vector_rank": None,
                    "keyword_rank": rank,
                    "vector_score": item.get("vector_score") or item.get("score"),
                    "bm25_score": item.get("bm25_score"),
                }

        # 收集所有 BM25 和向量分数，用于 min-max 归一化
        bm25_scores = [d["bm25_score"] for d in id_ranks.values() if d["bm25_score"] is not None]
        vector_scores = [d["vector_score"] for d in id_ranks.values() if d["vector_score"] is not None]

        bm25_min = min(bm25_scores) if bm25_scores else 0.0
        bm25_max = max(bm25_scores) if bm25_scores else 0.0
        bm25_range = bm25_max - bm25_min if bm25_max > bm25_min else 1.0

        vec_min = min(vector_scores) if vector_scores else 0.0
        vec_max = max(vector_scores) if vector_scores else 0.0
        vec_range = vec_max - vec_min if vec_max > vec_min else 1.0

        # 计算最大可能的 RRF 分数（用于归一化）
        max_rrf = 0.0
        for data in id_ranks.values():
            rrf = 0.0
            if data["vector_rank"] is not None:
                rrf += 1.0 / (k + data["vector_rank"])
            if data["keyword_rank"] is not None:
                rrf += 1.0 / (k + data["keyword_rank"])
            if rrf > max_rrf:
                max_rrf = rrf
        if max_rrf == 0:
            max_rrf = 1.0

        scored: list[tuple[float, dict[str, Any]]] = []
        for entry_id, data in id_ranks.items():
            # RRF 分数（归一化到 [0,1]）
            rrf = 0.0
            if data["vector_rank"] is not None:
                rrf += 1.0 / (k + data["vector_rank"])
            if data["keyword_rank"] is not None:
                rrf += 1.0 / (k + data["keyword_rank"])
            rrf_normalized = rrf / max_rrf

            # 归一化分数（BM25 + 向量，各归一化到 [0,1] 后取平均）
            norm_scores: list[float] = []
            if data["bm25_score"] is not None and bm25_scores:
                norm_scores.append((data["bm25_score"] - bm25_min) / bm25_range)
            if data["vector_score"] is not None and vector_scores:
                norm_scores.append((data["vector_score"] - vec_min) / vec_range)
            norm_score = sum(norm_scores) / len(norm_scores) if norm_scores else 0.0

            # 加权融合
            final_score = (1.0 - score_weight) * rrf_normalized + score_weight * norm_score
            scored.append((final_score, data["item"]))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]
