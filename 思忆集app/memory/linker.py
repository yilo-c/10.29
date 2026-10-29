# -*- coding: utf-8 -*-
"""MemoryLinker — 自动记忆关联 (MCP-12).

对标 Mem.ai 的 AI 自动组织能力。对每条新记忆，通过向量嵌入计算与已有记忆的语义相似度，
自动建立三级关联（强/中/弱），并维护反向链接、关联合并和关联质量报告。

设计文档: docs/58-MCP12-memory-linker.md
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from .linker_types import (
    STRONG_THRESHOLD,
    MEDIUM_THRESHOLD,
    WEAK_THRESHOLD,
    LINK_TYPE_STRONG,
    LINK_TYPE_MEDIUM,
    LINK_TYPE_WEAK,
    MemoryLink,
    MergeSuggestion,
)

logger = logging.getLogger("siyi.memory.linker")


class MemoryLinker:
    """自动记忆关联服务。

    用法:
        linker = MemoryLinker(data_store, embedding_service)
        # 新记忆入库后自动关联
        links = linker.link(new_memory, existing_memories)
        # 查关联 / 反向链接
        related = linker.get_linked_memories(memory_id)
        backlinks = linker.get_backlinks(memory_id)
        # 维护
        linker.cleanup_on_delete(memory_id)
    """

    def __init__(self, data_store, embedding_service=None):
        self.data_store = data_store
        self.embedding_service = embedding_service

    # ── 核心关联方法 ──

    def link(self, memory_entry: dict[str, Any],
             existing_memories: list[dict[str, Any]]) -> list[MemoryLink]:
        """为新记忆创建关联。

        Args:
            memory_entry: 新记忆条目，必须包含 entry_id / content
            existing_memories: 已有记忆列表

        Returns:
            新建的关联列表
        """
        entry_id = memory_entry.get("entry_id") or memory_entry.get("id", "")
        content = memory_entry.get("content") or memory_entry.get("title", "")
        if not entry_id or not content:
            logger.debug("link skipped: missing entry_id or content")
            return []

        existing_links = self._load_links()
        new_links: list[MemoryLink] = []

        for existing in existing_memories:
            existing_id = existing.get("entry_id") or existing.get("id", "")
            if not existing_id or existing_id == entry_id:
                continue
            if self._already_linked(existing_links, entry_id, existing_id):
                continue

            existing_content = existing.get("content") or existing.get("title", "")
            if not existing_content:
                continue

            similarity = self._compute_similarity(content, existing_content)
            if similarity < MEDIUM_THRESHOLD:
                # 弱关联（0.55-0.70）不自动创建链接，只在搜索时作为扩展结果展示
                continue

            link_type = self._classify_link(similarity)
            link = MemoryLink(
                source_id=entry_id,
                target_id=existing_id,
                similarity_score=similarity,
                link_type=link_type,
            )
            new_links.append(link)

        if new_links:
            all_links = existing_links + new_links
            self._save_links(all_links)

        return new_links

    def find_duplicates(self, content: str,
                        existing_memories: list[dict[str, Any]],
                        threshold: float = 0.85) -> list[dict[str, Any]]:
        """查找与给定内容高度相似的记忆（疑似重复）。

        Args:
            content: 待检查的内容
            existing_memories: 已有记忆列表
            threshold: 相似度阈值，默认 0.85

        Returns:
            疑似重复的记忆列表
        """
        if not content or not existing_memories:
            return []
        results = []
        for entry in existing_memories:
            entry_content = entry.get("content") or entry.get("title", "")
            if not entry_content:
                continue
            score = self._compute_similarity(content, entry_content)
            if score >= threshold:
                results.append({
                    **entry,
                    "_similarity_score": score,
                })
        results.sort(key=lambda x: x.get("_similarity_score", 0), reverse=True)
        return results

    def get_weak_extension_results(
        self,
        content: str,
        existing_memories: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """查找弱关联记忆作为搜索扩展结果（类似"您可能还想看"）。

        Args:
            content: 查询内容
            existing_memories: 已有记忆列表
            top_k: 最大返回条数

        Returns:
            弱关联记忆列表，按相似度降序排列
        """
        if not content or not existing_memories:
            return []
        results = []
        for entry in existing_memories:
            entry_content = entry.get("content") or entry.get("title", "")
            if not entry_content:
                continue
            entry_id = entry.get("entry_id") or entry.get("id", "")
            if not entry_id:
                continue
            score = self._compute_similarity(content, entry_content)
            if WEAK_THRESHOLD <= score < MEDIUM_THRESHOLD:
                results.append({
                    **entry,
                    "_similarity_score": round(score, 4),
                    "_link_type": LINK_TYPE_WEAK,
                })
        results.sort(key=lambda x: x.get("_similarity_score", 0), reverse=True)
        return results[:top_k]

    def suggest_merge(self, memory_a: dict[str, Any],
                      memory_b: dict[str, Any]) -> MergeSuggestion:
        """对两条强关联记忆生成合并建议。

        Args:
            memory_a: 记忆 A
            memory_b: 记忆 B

        Returns:
            合并建议
        """
        content_a = (memory_a.get("content") or memory_a.get("title", "")).strip()
        content_b = (memory_b.get("content") or memory_b.get("title", "")).strip()
        similarity = self._compute_similarity(content_a, content_b)

        diffs = self._find_differences(memory_a, memory_b)

        action = "keep_both"
        if similarity > 0.92:
            action = "merge"
        elif similarity > 0.85 and len(diffs) <= 2:
            action = "merge"

        return MergeSuggestion(
            memory_a=memory_a,
            memory_b=memory_b,
            similarity_score=similarity,
            differences=diffs,
            suggested_action=action,
        )

    # ── 查询方法 ──

    def get_linked_memories(self, memory_id: str,
                            link_type: str | None = None) -> list[dict[str, Any]]:
        """获取与指定记忆关联的所有记忆。

        Args:
            memory_id: 记忆 ID
            link_type: 可选过滤，指定关联类型

        Returns:
            关联记忆链接列表
        """
        all_links = self._load_links()
        results = []
        for link in all_links:
            if link.source_id == memory_id:
                if link_type and link.link_type != link_type:
                    continue
                results.append(link.to_dict())
            elif link.target_id == memory_id:
                if link_type and link.link_type != link_type:
                    continue
                results.append(link.to_dict())
        return results

    def get_backlinks(self, memory_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """获取反向链接：有多少其他记忆关联到本条。

        Args:
            memory_id: 记忆 ID
            limit: 最大返回条数

        Returns:
            反向链接列表，按创建时间倒序
        """
        all_links = self._load_links()
        backlinks = [
            link.to_dict()
            for link in all_links
            if link.target_id == memory_id
        ]
        backlinks.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return backlinks[:limit]

    def get_backlink_count(self, memory_id: str) -> int:
        """获取反向链接数量（作为记忆重要性的参考指标）。"""
        return len(self.get_backlinks(memory_id, limit=9999))

    def get_linked_memories_with_details(
        self,
        memory_id: str,
        memory_lookup: dict[str, dict[str, Any]],
        link_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取关联记忆的详细信息（标题、摘要、关联强度标签）。

        Args:
            memory_id: 记忆 ID
            memory_lookup: {memory_id: memory_entry} 映射表
            link_type: 可选过滤关联类型

        Returns:
            每条记录: memory_id, title, snippet, link_type, link_label, similarity_score, linked_at
        """
        links = self.get_linked_memories(memory_id, link_type)
        results: list[dict[str, Any]] = []

        for link in links:
            linked_id = link["target_id"] if link["source_id"] == memory_id else link["source_id"]
            entry = memory_lookup.get(linked_id, {})
            content = entry.get("content") or entry.get("title", "")
            snippet = content[:120] if len(content) > 120 else content

            link_label = {
                LINK_TYPE_STRONG: "强关联",
                LINK_TYPE_MEDIUM: "相关",
                LINK_TYPE_WEAK: "可能相关",
            }.get(link.get("link_type", ""), link.get("link_type", ""))

            results.append({
                "memory_id": linked_id,
                "title": entry.get("title") or entry.get("snippet", linked_id)[:60],
                "snippet": snippet,
                "link_type": link.get("link_type", ""),
                "link_label": link_label,
                "similarity_score": link.get("similarity_score", 0.0),
                "linked_at": link.get("created_at", ""),
            })

        type_order = {LINK_TYPE_STRONG: 0, LINK_TYPE_MEDIUM: 1, LINK_TYPE_WEAK: 2}
        results.sort(key=lambda x: (type_order.get(x["link_type"], 9), -x["similarity_score"]))
        return results

    # ── 维护方法 ──

    def cleanup_on_delete(self, memory_id: str) -> int:
        """删除一条记忆时，清理所有关联引用。

        Args:
            memory_id: 被删除的记忆 ID

        Returns:
            清理的关联数量
        """
        all_links = self._load_links()
        before = len(all_links)
        all_links = [
            link for link in all_links
            if link.source_id != memory_id and link.target_id != memory_id
        ]
        removed = before - len(all_links)
        if removed > 0:
            self._save_links(all_links)
            logger.info("cleanup_on_delete: removed %d links for memory_id=%s", removed, memory_id)
        return removed

    def reindex_memory(self, memory_id: str, new_content: str,
                       existing_memories: list[dict[str, Any]]) -> list[MemoryLink]:
        """记忆内容更新后，删除旧关联并重新计算。

        Args:
            memory_id: 更新的记忆 ID
            new_content: 更新后的内容
            existing_memories: 已有记忆列表（排除自身）

        Returns:
            新建的关联列表
        """
        self.cleanup_on_delete(memory_id)
        dummy_entry = {"entry_id": memory_id, "content": new_content}
        return self.link(dummy_entry, existing_memories)

    def periodic_recalculate(
        self,
        all_memories: list[dict[str, Any]],
        max_pairs: int = 200,
    ) -> list[MemoryLink]:
        """定期重新计算低相关度记忆的关联。

        因为新记忆可能提供新的关联视角，需要定期（每小时/每天）对目前
        关联数较少或只有弱关联的记忆进行重新计算。

        Args:
            all_memories: 全部记忆列表
            max_pairs: 最大计算配对数量（性能保护）

        Returns:
            新建立的关联列表
        """
        if not all_memories:
            return []

        existing_links = self._load_links()

        # 找出需要重新计算的记忆：关联数少 或 只有弱关联
        memory_link_counts: dict[str, int] = {}
        memory_has_only_weak: set[str] = set()
        for link in existing_links:
            for mid in (link.source_id, link.target_id):
                memory_link_counts[mid] = memory_link_counts.get(mid, 0) + 1
            if link.link_type == LINK_TYPE_WEAK:
                memory_has_only_weak.add(link.source_id)
                memory_has_only_weak.add(link.target_id)

        candidates: list[str] = []
        for entry in all_memories:
            eid = entry.get("entry_id") or entry.get("id", "")
            if not eid:
                continue
            count = memory_link_counts.get(eid, 0)
            if count < 2 or eid in memory_has_only_weak:
                candidates.append(eid)

        if not candidates:
            logger.debug("periodic_recalculate: no candidates found")
            return []

        mem_map: dict[str, dict[str, Any]] = {}
        for entry in all_memories:
            eid = entry.get("entry_id") or entry.get("id", "")
            if eid:
                mem_map[eid] = entry

        new_links: list[MemoryLink] = []
        pairs_checked = 0

        # C3 优化：预构建已链接对的集合，O(1) 查找替代 O(L) 线性扫描
        # 规范化为 (min_id, max_id) 元组以支持双向匹配
        linked_pairs: set[tuple[str, str]] = set()
        for link in existing_links:
            pair = (min(link.source_id, link.target_id), max(link.source_id, link.target_id))
            linked_pairs.add(pair)

        for src_id in candidates:
            src_entry = mem_map.get(src_id)
            if not src_entry:
                continue
            src_content = src_entry.get("content") or src_entry.get("title", "")
            if not src_content:
                continue

            for entry in all_memories:
                dst_id = entry.get("entry_id") or entry.get("id", "")
                if not dst_id or dst_id == src_id:
                    continue
                # C3 优化：O(1) 集合查找替代 O(L) 线性扫描 + 列表拼接
                pair = (min(src_id, dst_id), max(src_id, dst_id))
                if pair in linked_pairs:
                    continue

                dst_content = entry.get("content") or entry.get("title", "")
                if not dst_content:
                    continue

                similarity = self._compute_similarity(src_content, dst_content)
                if similarity < MEDIUM_THRESHOLD:
                    continue

                pairs_checked += 1
                if pairs_checked > max_pairs:
                    break

                link_type = self._classify_link(similarity)
                link = MemoryLink(
                    source_id=src_id,
                    target_id=dst_id,
                    similarity_score=similarity,
                    link_type=link_type,
                )
                existing_links.append(link)
                new_links.append(link)
                linked_pairs.add(pair)

            if pairs_checked > max_pairs:
                break

        if new_links:
            self._save_links(existing_links)
            logger.info(
                "periodic_recalculate: created %d new links from %d candidates",
                len(new_links), len(candidates),
            )

        return new_links

    def merge_link_networks(
        self,
        keep_id: str,
        remove_id: str,
    ) -> int:
        """当两条记忆被用户手动合并时，合并它们的关联网络。

        将 remove_id 的所有关联重定向到 keep_id，然后清理 remove_id 的关联。

        Args:
            keep_id: 合并后保留的记忆 ID
            remove_id: 被合并掉的记忆 ID

        Returns:
            重定向的关联数量
        """
        if not keep_id or not remove_id or keep_id == remove_id:
            return 0

        all_links = self._load_links()
        redirected = 0

        for link in all_links:
            if link.source_id == remove_id:
                link.source_id = keep_id
                redirected += 1
            if link.target_id == remove_id:
                link.target_id = keep_id
                redirected += 1

        # 移除自关联
        all_links = [
            link for link in all_links
            if link.source_id != link.target_id
        ]

        # 去重：保留相似度最高的链接
        seen: set[tuple[str, str]] = set()
        unique_links: list[MemoryLink] = []
        for link in sorted(all_links, key=lambda l: l.similarity_score, reverse=True):
            pair = (link.source_id, link.target_id)
            pair_rev = (link.target_id, link.source_id)
            if pair not in seen and pair_rev not in seen:
                seen.add(pair)
                unique_links.append(link)

        if len(unique_links) != len(all_links):
            logger.debug(
                "merge_link_networks: dedup removed %d links",
                len(all_links) - len(unique_links),
            )

        self._save_links(unique_links)
        logger.info(
            "merge_link_networks: redirected %d links from %s to %s",
            redirected, remove_id, keep_id,
        )
        return redirected

    def get_link_stats(self, total_memories: int = 0) -> dict[str, Any]:
        """获取关联质量报告。

        Args:
            total_memories: 总记忆数（用于计算覆盖率）

        Returns:
            关联统计
        """
        all_links = self._load_links()
        if not all_links:
            return {
                "total_links": 0,
                "coverage_rate": 0.0,
                "avg_links_per_memory": 0.0,
                "by_type": {"strong": 0, "medium": 0, "weak": 0},
            }

        linked_ids: set[str] = set()
        type_counts: dict[str, int] = {"strong": 0, "medium": 0, "weak": 0}
        source_ids: set[str] = set()

        for link in all_links:
            source_ids.add(link.source_id)
            linked_ids.add(link.source_id)
            linked_ids.add(link.target_id)
            if link.link_type in type_counts:
                type_counts[link.link_type] += 1

        coverage = 0.0
        if total_memories > 0:
            coverage = round(len(linked_ids) / total_memories, 4)

        avg_links = round(len(all_links) / max(len(source_ids), 1), 2)

        return {
            "total_links": len(all_links),
            "unique_memories_linked": len(linked_ids),
            "coverage_rate": coverage,
            "avg_links_per_memory": avg_links,
            "by_type": type_counts,
        }

    def upgrade_weak_links(self, memory_ids: list[str],
                           existing_memories: list[dict[str, Any]]) -> int:
        """将用户同时访问的弱关联升级为中等关联。

        如果用户多次同时访问两条弱关联记忆，调用此方法升级。

        Args:
            memory_ids: 用户同时访问的记忆 ID 列表
            existing_memories: 全部记忆列表

        Returns:
            升级的关联数量
        """
        if len(memory_ids) < 2:
            return 0

        all_links = self._load_links()
        upgraded = 0

        for i, src_id in enumerate(memory_ids):
            for dst_id in memory_ids[i + 1:]:
                for link in all_links:
                    pair = (link.source_id == src_id and link.target_id == dst_id)
                    pair_rev = (link.source_id == dst_id and link.target_id == src_id)
                    if not (pair or pair_rev):
                        continue
                    if link.link_type == LINK_TYPE_WEAK:
                        link.link_type = LINK_TYPE_MEDIUM
                        upgraded += 1
                        logger.debug("upgraded weak link: %s <-> %s", src_id, dst_id)

        if upgraded > 0:
            self._save_links(all_links)

        return upgraded

    # ── 共访问跟踪 ──

    CO_ACCESS_UPGRADE_THRESHOLD = 3
    CO_ACCESS_KEY = "assistant_memory_co_access"

    def record_co_access(self, memory_id_a: str, memory_id_b: str) -> bool:
        """记录用户同时访问两条记忆的行为。

        如果两条记忆之间存在弱关联且共访问次数达到阈值，自动升级为中等关联。

        Args:
            memory_id_a: 记忆 A ID
            memory_id_b: 记忆 B ID

        Returns:
            是否升级了关联
        """
        if not memory_id_a or not memory_id_b or memory_id_a == memory_id_b:
            return False

        sorted_ids = sorted([memory_id_a, memory_id_b])
        pair_key = f"{sorted_ids[0]}|{sorted_ids[1]}"

        records = self._load_co_access()
        pair = records.get(pair_key, {"count": 0, "last_access": ""})
        pair["count"] = pair["count"] + 1
        pair["last_access"] = datetime.now(timezone.utc).isoformat()
        records[pair_key] = pair
        self._save_co_access(records)

        if pair["count"] >= self.CO_ACCESS_UPGRADE_THRESHOLD:
            all_links = self._load_links()
            upgraded = False
            for link in all_links:
                pair_ab = link.source_id == memory_id_a and link.target_id == memory_id_b
                pair_ba = link.source_id == memory_id_b and link.target_id == memory_id_a
                if not (pair_ab or pair_ba):
                    continue
                if link.link_type == LINK_TYPE_WEAK:
                    link.link_type = LINK_TYPE_MEDIUM
                    upgraded = True
                    logger.debug(
                        "co-access upgrade: %s <-> %s (count=%d)",
                        memory_id_a, memory_id_b, pair["count"],
                    )
                    break
            if upgraded:
                self._save_links(all_links)
                return True

        return False

    def get_co_access_count(self, memory_id_a: str, memory_id_b: str) -> int:
        """获取两条记忆之间的共访问次数。"""
        if not memory_id_a or not memory_id_b:
            return 0
        sorted_ids = sorted([memory_id_a, memory_id_b])
        pair_key = f"{sorted_ids[0]}|{sorted_ids[1]}"
        records = self._load_co_access()
        pair = records.get(pair_key, {})
        return pair.get("count", 0)

    # ── 内部方法 ──

    def _compute_similarity(self, text_a: str, text_b: str) -> float:
        """计算两条文本的语义相似度。

        优先级：
        1. 嵌入服务（embedding_service）余弦相似度 — 最佳
        2. TF-IDF 余弦相似度 — 中文场景下比 Jaccard 精度更高
        3. Jaccard 词/bigram 重叠 — 最终回退
        """
        if not text_a or not text_b:
            return 0.0

        # 完全匹配
        if text_a.strip().lower() == text_b.strip().lower():
            return 1.0

        if self.embedding_service is not None:
            try:
                emb_a = self.embedding_service.get_embedding(text_a)
                emb_b = self.embedding_service.get_embedding(text_b)
                return _cosine_similarity(emb_a, emb_b)
            except Exception as exc:
                logger.info("embedding similarity failed, fallback to tf-idf: %s", exc, exc_info=True)

        # TF-IDF 回退（比 Jaccard 精度更高，特别是中文场景）
        try:
            return _tfidf_cosine_similarity(text_a, text_b)
        except Exception as exc:
            logger.debug("TF-IDF similarity failed, fallback to jaccard: %s", exc)
            return _jaccard_similarity(text_a, text_b)

    def _classify_link(self, similarity: float) -> str:
        if similarity >= STRONG_THRESHOLD:
            return LINK_TYPE_STRONG
        elif similarity >= MEDIUM_THRESHOLD:
            return LINK_TYPE_MEDIUM
        return LINK_TYPE_WEAK

    def _load_links(self) -> list[MemoryLink]:
        raw = self.data_store.load_json("assistant_memory_links", [])
        if not isinstance(raw, list):
            return []
        return [MemoryLink.from_dict(item) for item in raw if isinstance(item, dict)]

    def _save_links(self, links: list[MemoryLink]) -> None:
        self.data_store.save_json(
            "assistant_memory_links",
            [link.to_dict() for link in links],
        )

    def _load_co_access(self) -> dict[str, Any]:
        raw = self.data_store.load_json(self.CO_ACCESS_KEY, {})
        if not isinstance(raw, dict):
            return {}
        return raw

    def _save_co_access(self, records: dict[str, Any]) -> None:
        self.data_store.save_json(self.CO_ACCESS_KEY, records)

    @staticmethod
    def _already_linked(links: list[MemoryLink], source_id: str,
                        target_id: str) -> bool:
        for link in links:
            if (link.source_id == source_id and link.target_id == target_id):
                return True
            if (link.source_id == target_id and link.target_id == source_id):
                return True
        return False

    @staticmethod
    def _find_differences(memory_a: dict[str, Any],
                          memory_b: dict[str, Any]) -> list[str]:
        """找出两条记忆的主要差异点。"""
        diffs = []
        for key in ("content", "title", "type", "source_type"):
            val_a = str(memory_a.get(key, "")).strip()
            val_b = str(memory_b.get(key, "")).strip()
            if val_a and val_b and val_a != val_b:
                diffs.append(f"{key}: '{val_a}' vs '{val_b}'")
        return diffs


# ── 工具函数 ──

def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """计算两条文本的 Jaccard 相似度（回退方案）。

    对于空格分隔的文本使用词级比较（英文/拼音）；
    对于无空格文本（如中文）使用字符级 bigram 比较。
    """
    a = text_a.lower().strip()
    b = text_b.lower().strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    # 如果文本包含空格，用词级比较
    if " " in a or " " in b:
        words_a = set(a.split())
        words_b = set(b.split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)

    # 无空格文本（如中文）：使用字符级 bigram
    bigrams_a = {a[i:i+2] for i in range(max(0, len(a) - 1))}
    bigrams_b = {b[i:i+2] for i in range(max(0, len(b) - 1))}
    if not bigrams_a or not bigrams_b:
        return 0.0
    intersection = bigrams_a & bigrams_b
    union = bigrams_a | bigrams_b
    return len(intersection) / len(union)


def _tokenize_for_tfidf(text: str) -> list[str]:
    """TF-IDF 用的分词函数。

    对于含空格的文本（英文/拼音）用空格分词；
    对于无空格的中文文本用字符级 bigram 分词。
    """
    text = text.lower().strip()
    if not text:
        return []
    if " " in text:
        return [t for t in text.split() if len(t) > 0]
    # 中文 bigram
    return [text[i:i+2] for i in range(max(0, len(text) - 1))]


def _tfidf_cosine_similarity(text_a: str, text_b: str) -> float:
    """基于 TF-IDF 的余弦相似度。

    比纯 Jaccard 精度更高，因为：
    1. 考虑词频（TF）：多次出现的词权重更高
    2. 考虑逆文档频率（IDF）：罕见词权重更高
    3. 用余弦相似度而非简单重叠率

    由于只有两条文本，IDF 用两条文本作为文档集计算：
    - 在两条文本中都出现的词：IDF = log(2/2) = 0（权重为 0）
    - 仅在一条文本中出现的词：IDF = log(2/1) ≈ 0.693

    这意味着共同词不会贡献相似度，只有独特词的差异才会影响。
    为避免此问题，实际使用 TF * 1（简化 IDF=1），即纯 TF 余弦相似度。
    """
    tokens_a = _tokenize_for_tfidf(text_a)
    tokens_b = _tokenize_for_tfidf(text_b)
    if not tokens_a or not tokens_b:
        return 0.0

    # 计算词频
    from collections import Counter
    tf_a = Counter(tokens_a)
    tf_b = Counter(tokens_b)

    # 构建词汇表
    vocab = set(tf_a.keys()) | set(tf_b.keys())
    if not vocab:
        return 0.0

    # 构建 TF 向量（IDF 简化为 1，因为只有两条文本时 IDF 区分度有限）
    vec_a = [float(tf_a.get(term, 0)) for term in vocab]
    vec_b = [float(tf_b.get(term, 0)) for term in vocab]

    # 余弦相似度
    return _cosine_similarity(vec_a, vec_b)
