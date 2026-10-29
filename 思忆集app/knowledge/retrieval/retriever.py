# -*- coding: utf-8 -*-
"""
混合检索器 - 结合向量检索 (ChromaDB) 和关键词检索 (BM25)

设计规范：
- 向量检索: top_k=20, 相似度阈值 0.6
- 关键词检索 (BM25): top_k=10
- 合并去重 → (可选重排序) → 取 top_k=5
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False


class BM25Okapi:
    """
    轻量 BM25 实现，基于 jieba 中文分词。

    参考资料: https://en.wikipedia.org/wiki/Okapi_BM25
    """

    def __init__(self, corpus: List[str], k1: float = 1.5, b: float = 0.75):
        """
        Args:
            corpus: 文档列表
            k1: BM25 参数，控制词频饱和度
            b: BM25 参数，控制文档长度归一化
        """
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus)
        self.doc_freqs: List[Counter] = []
        self.doc_lengths: List[int] = []
        self.avg_doc_length: float = 0.0
        self.idf: Dict[str, float] = {}
        self._build_index(corpus)

    def _tokenize(self, text: str) -> List[str]:
        """分词：优先使用 jieba 精确模式，回退到空格/标点分割"""
        if JIEBA_AVAILABLE:
            return list(jieba.cut(text))
        # 简单 fallback：按非中文/字母/数字字符分割
        tokens: List[str] = []
        current: List[str] = []
        for ch in text:
            if ch.isalnum():
                current.append(ch.lower())
            else:
                if current:
                    tokens.append("".join(current))
                    current = []
        if current:
            tokens.append("".join(current))
        return tokens

    def _build_index(self, corpus: List[str]) -> None:
        """构建 BM25 索引"""
        total_length = 0
        df: Counter = Counter()

        for doc in corpus:
            tokens = self._tokenize(doc)
            self.doc_lengths.append(len(tokens))
            total_length += len(tokens)
            # 文档级词频（用于计算文档频率 DF）
            unique_tokens = set(tokens)
            self.doc_freqs.append(Counter(tokens))
            for token in unique_tokens:
                df[token] += 1

        self.avg_doc_length = total_length / max(self.corpus_size, 1)

        # 计算 IDF
        for token, freq in df.items():
            self.idf[token] = math.log(
                1 + (self.corpus_size - freq + 0.5) / (freq + 0.5)
            )

    def get_scores(self, query: str) -> List[float]:
        """对 corpus 中的每个文档计算 BM25 分数"""
        query_tokens = self._tokenize(query)
        scores = [0.0] * self.corpus_size

        for q_token in query_tokens:
            if q_token not in self.idf:
                continue
            q_idf = self.idf[q_token]

            for i in range(self.corpus_size):
                tf = self.doc_freqs[i].get(q_token, 0)
                if tf == 0:
                    continue
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (
                    1 - self.b + self.b * self.doc_lengths[i] / self.avg_doc_length
                )
                scores[i] += q_idf * (numerator / denominator)

        return scores


class HybridRetriever:
    """
    混合检索器：向量检索 + BM25 关键词检索

    用法:
        retriever = HybridRetriever(vector_store)
        results = retriever.query("你的问题", top_k=5)
    """

    # 默认检索参数（设计规范）
    VECTOR_TOP_K = 20
    VECTOR_SIMILARITY_THRESHOLD = 0.6
    BM25_TOP_K = 10
    FINAL_TOP_K = 5

    def __init__(self, vector_store):
        """
        Args:
            vector_store: ChromaVectorStore 实例
        """
        self._vector_store = vector_store
        self._b_indexes: Dict[str, BM25Okapi] = {}  # collection_name -> BM25
        self._bm25_dirty = True

    # ── 公开接口 ──────────────────────────────────────────────────────

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        where: Optional[Dict[str, Any]] = None,
        alpha: float = 0.7,
    ) -> Dict[str, Any]:
        """
        混合检索主入口

        Args:
            query_text: 查询文本
            top_k: 最终返回结果数
            where: 元数据过滤条件
            alpha: 向量分数权重 (0~1)，1=纯向量，0=纯 BM25

        Returns:
            {
                "documents": [...],
                "metadatas": [...],
                "distances": [...],
                "scores": [...],       # 融合后的分数
                "vector_scores": [...],
                "bm25_scores": [...],
            }
        """
        # 1. 向量检索
        vector_results = self._vector_search(query_text, where=where)
        # 2. BM25 关键词检索
        bm25_results = self._bm25_search(query_text, where=where)
        # 3. 融合并去重
        fused = self._fuse_results(
            vector_results, bm25_results,
            alpha=alpha, top_k=top_k,
        )
        return fused

    # ── 索引管理 ──────────────────────────────────────────────────────

    def rebuild_bm25_index(self, collection_name: Optional[str] = None) -> None:
        """重建 BM25 索引（文档变更后调用）"""
        all_docs = self._vector_store.get_all_documents()
        if not all_docs or not all_docs.get("documents"):
            logger.debug("No documents to index for BM25")
            return
        docs: List[str] = list(all_docs["documents"])
        cn = collection_name or self._vector_store.collection_name
        self._b_indexes[cn] = BM25Okapi(docs)
        self._bm25_dirty = False
        logger.info("BM25 index rebuilt: %d documents in '%s'", len(docs), cn)

    def mark_index_dirty(self) -> None:
        """标记 BM25 索引为脏（添加/删除文档后调用）"""
        self._bm25_dirty = True

    # ── 内部实现 ──────────────────────────────────────────────────────

    def _ensure_bm25_index(self) -> None:
        """确保 BM25 索引已构建"""
        if self._bm25_dirty or self._vector_store.collection_name not in self._b_indexes:
            self.rebuild_bm25_index()

    def _vector_search(
        self,
        query_text: str,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """向量检索（ChromaDB）"""
        try:
            results = self._vector_store.query(
                query_texts=[query_text],
                n_results=self.VECTOR_TOP_K,
                where=where,
            )
        except Exception as e:
            logger.warning("Vector search failed: %s", e)
            results = {"documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]]}

        items: List[Dict[str, Any]] = []
        docs = results.get("documents", [[]])[0] or []
        metas = results.get("metadatas", [[]])[0] or []
        dists = results.get("distances", [[]])[0] or []
        ids = results.get("ids", [[]])[0] or []

        for i in range(len(docs)):
            similarity = 1.0 - (dists[i] if i < len(dists) else 0.0)
            if similarity < self.VECTOR_SIMILARITY_THRESHOLD:
                continue
            items.append({
                "id": ids[i] if i < len(ids) else "",
                "document": docs[i],
                "metadata": metas[i] if i < len(metas) else {},
                "distance": dists[i] if i < len(dists) else 0.0,
                "similarity": similarity,
            })

        logger.debug("Vector search returned %d items (threshold=%.2f)", len(items), self.VECTOR_SIMILARITY_THRESHOLD)
        return items

    def _bm25_search(
        self,
        query_text: str,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """BM25 关键词检索"""
        self._ensure_bm25_index()
        bm25 = self._b_indexes.get(self._vector_store.collection_name)
        if bm25 is None or bm25.corpus_size == 0:
            return []

        all_docs = self._vector_store.get_all_documents()
        all_docs_list = list(all_docs.get("documents") or [])
        all_metas = list(all_docs.get("metadatas") or [])
        all_ids = list(all_docs.get("ids") or [])

        if not all_docs_list:
            return []

        scores = bm25.get_scores(query_text)

        # 应用 where 过滤
        indexed = list(range(len(all_docs_list)))
        if where:
            indexed = [
                i for i in indexed
                if i < len(all_metas) and self._metadata_match(all_metas[i], where)
            ]

        # 按分数排序，取 top_k
        indexed.sort(key=lambda i: scores[i], reverse=True)
        indexed = [i for i in indexed if scores[i] > 0][:self.BM25_TOP_K]

        items = []
        for i in indexed:
            # 归一化 BM25 分数到 [0, 1] 区间
            max_score = max(scores) if scores else 1.0
            normalized = scores[i] / max(max_score, 1e-8)
            items.append({
                "id": all_ids[i] if i < len(all_ids) else "",
                "document": all_docs_list[i],
                "metadata": all_metas[i] if i < len(all_metas) else {},
                "similarity": normalized,
                "distance": 1.0 - normalized,
            })

        logger.debug("BM25 search returned %d items", len(items))
        return items

    @staticmethod
    def _fuse_results(
        vector_items: List[Dict[str, Any]],
        bm25_items: List[Dict[str, Any]],
        alpha: float = 0.7,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """
        融合向量 + BM25 结果（加权平均 + 去重）

        使用 Reciprocal Rank Fusion (RRF) 风格的加权融合。
        """
        # 按文档内容去重
        seen: Set[str] = set()
        fused: List[Dict[str, Any]] = []

        # 先处理向量结果（赋予权重 alpha）
        for item in vector_items:
            doc = item["document"]
            doc_sig = doc[:200] if doc else ""
            if doc_sig in seen:
                continue
            seen.add(doc_sig)
            item["_vector_score"] = item["similarity"]
            item["_bm25_score"] = 0.0
            item["_fusion_score"] = item["similarity"] * alpha
            fused.append(item)

        # 融合 BM25 结果
        for item in bm25_items:
            doc = item["document"]
            doc_sig = doc[:200] if doc else ""
            if doc_sig in seen:
                # 已有该项，融合 BM25 分数
                for existing in fused:
                    if existing["document"][:200] == doc_sig:
                        existing["_bm25_score"] = item["similarity"]
                        existing["_fusion_score"] = (
                            existing.get("_vector_score", 0) * alpha
                            + item["similarity"] * (1 - alpha)
                        )
                        break
            else:
                seen.add(doc_sig)
                item["_vector_score"] = 0.0
                item["_bm25_score"] = item["similarity"]
                item["_fusion_score"] = item["similarity"] * (1 - alpha)
                fused.append(item)

        # 按融合分数排序
        fused.sort(key=lambda x: x["_fusion_score"], reverse=True)

        # 截取 top_k
        top = fused[:top_k]

        return {
            "documents": [item["document"] for item in top],
            "metadatas": [item["metadata"] for item in top],
            "distances": [1.0 - item["_fusion_score"] for item in top],
            "scores": [item["_fusion_score"] for item in top],
            "vector_scores": [item.get("_vector_score", 0) for item in top],
            "bm25_scores": [item.get("_bm25_score", 0) for item in top],
        }

    @staticmethod
    def _metadata_match(metadata: Any, where: Dict[str, Any]) -> bool:
        """检查元数据是否匹配过滤条件（仅支持简单 $eq）"""
        if not isinstance(metadata, dict):
            return False
        for key, value in where.items():
            if isinstance(value, dict):
                # 支持 ChromaDB 操作符如 $eq, $ne, $gt, $gte, $lt, $lte
                for op, op_val in value.items():
                    meta_val = metadata.get(key)
                    if op == "$eq" and meta_val != op_val:
                        return False
                    elif op == "$ne" and meta_val == op_val:
                        return False
                    elif op == "$gt" and not (meta_val is not None and meta_val > op_val):
                        return False
                    elif op == "$gte" and not (meta_val is not None and meta_val >= op_val):
                        return False
                    elif op == "$lt" and not (meta_val is not None and meta_val < op_val):
                        return False
                    elif op == "$lte" and not (meta_val is not None and meta_val <= op_val):
                        return False
            else:
                if metadata.get(key) != value:
                    return False
        return True
