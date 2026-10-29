# -*- coding: utf-8 -*-
"""批3（2026-09-16）：W1 出处拆层 + W2 兜底删除 + 遗留边迁移的回归测试。

对应审计 deliverables/knowledge-graph-pipeline-audit-2026-09-16.md：

- W1：出处元数据（mentioned_in + mem_* 容器实体）冒充语义关系，占关系表
  86%。新契约：抽取时出处写 ``entity.properties.sources``，不建边、不建容器。
- W2：``_normalize_relation`` 末行无条件 ``return REL_RELATED_TO`` 把未识别
  谓词伪造成泛关系。新契约：未命中返回 None → 调用方丢弃并计数
  ``relation_dropped``；显式 related_to/相关/关联 仍合法。
- 增量迁移：抽取碰到带遗留 mentioned_in 边的实体时，就地迁移为
  properties.sources 并删除边与孤儿容器（幂等）。
"""
from __future__ import annotations

import json

from knowledge.graph_extractor import KnowledgeGraphExtractor
from knowledge.graph_store import (
    REL_MENTIONED_IN,
    REL_PART_OF,
    REL_RELATED_TO,
    KnowledgeGraphStore,
)


class _FakeAI:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete_text(self, messages, **kwargs):  # noqa: ANN001
        return self._reply


def _mk_store(tmp_path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(path=tmp_path / "kg.json")


# ---------------------------------------------------------------- W1


def test_patterns_path_records_provenance_not_edges(tmp_path) -> None:
    """正则通道：出处进 properties.sources，relations 里不得出现 mentioned_in。"""
    store = _mk_store(tmp_path)
    ex = KnowledgeGraphExtractor(store)
    result = ex._extract_with_patterns("张伟说要去北京市开会", source_id="note:42")
    store.save()

    rels = store.get_all_relations()
    assert all(r.get("relation_type") != REL_MENTIONED_IN for r in rels), (
        f"W1 违规：relations 出现出处边 {rels}"
    )
    # 容器实体不得新建（mem_* / is_memory）
    for e in store.get_all_entities():
        assert not (e.get("properties") or {}).get("is_memory"), "W1 违规：又建了容器实体"
        assert not str(e.get("id", "")).startswith("mem_"), "W1 违规：容器 id 前缀再现"

    # 至少一个实体带上了出处
    sources = [
        (e.get("properties") or {}).get("sources") or []
        for e in store.get_all_entities()
    ]
    assert any("note:42" in s for s in sources), f"出处未写入 properties.sources: {sources}"
    assert result.get("via") == "patterns"


def test_llm_path_records_provenance_not_edges(tmp_path) -> None:
    """LLM 通道：同上，出处拆层。"""
    store = _mk_store(tmp_path)
    ex = KnowledgeGraphExtractor(store)
    ex.ai_client = _FakeAI(json.dumps({
        "entities": [
            {"name": "DeepSeek", "type": "organization", "aliases": []},
            {"name": "思忆集", "type": "project", "aliases": []},
        ],
        "relations": [
            {"source": "思忆集", "target": "DeepSeek", "relation": "apply_to"},
        ],
    }))
    result = ex._extract_with_llm("思忆集采用 DeepSeek", source_id="deepseek:conv-1")

    rels = store.get_all_relations()
    assert all(r.get("relation_type") != REL_MENTIONED_IN for r in rels)
    assert result.get("via") == "llm"
    assert len(result.get("relations") or []) == 1  # 返回的是入库成功的语义边
    src_lists = [
        (e.get("properties") or {}).get("sources") or []
        for e in store.get_all_entities()
    ]
    assert sum(1 for s in src_lists if "deepseek:conv-1" in s) == 2, (
        f"两个实体都应记录出处: {src_lists}"
    )


def test_provenance_dedup_and_no_rename(tmp_path) -> None:
    """add_entity_source 去重，且不触碰 name/type（拆掉静默改名风险）。"""
    store = _mk_store(tmp_path)
    store.add_entity("e1", "阿尔法", "concept")
    assert store.add_entity_source("e1", "note:1") is True
    assert store.add_entity_source("e1", "note:1") is False  # 去重
    assert store.add_entity_source("e1", "note:2") is True
    e = store.get_entity("e1")
    assert e["name"] == "阿尔法" and e["type"] == "concept"
    assert (e["properties"] or {}).get("sources") == ["note:1", "note:2"]
    assert store.add_entity_source("ghost", "note:3") is False  # 实体不存在不崩


# ---------------------------------------------------------------- W2


def test_normalize_relation_unknown_returns_none() -> None:
    """未识别谓词 → None（丢弃），不再伪造 related_to。"""
    assert KnowledgeGraphExtractor._normalize_relation("包含并依赖底层组件") is None
    assert KnowledgeGraphExtractor._normalize_relation("whatever") is None
    assert KnowledgeGraphExtractor._normalize_relation("") is None


def test_normalize_relation_explicit_related_to_still_valid() -> None:
    """显式 related_to / 相关 / 关联 仍合法——白名单而非兜底。"""
    assert KnowledgeGraphExtractor._normalize_relation("related_to") == REL_RELATED_TO
    assert KnowledgeGraphExtractor._normalize_relation("相关") == REL_RELATED_TO
    assert KnowledgeGraphExtractor._normalize_relation("支持") == "support"
    assert KnowledgeGraphExtractor._normalize_relation("属于") == REL_PART_OF


def test_llm_unknown_predicate_dropped_and_counted(tmp_path) -> None:
    """LLM 给出垃圾谓词 → 边被丢弃 + relation_dropped 计数，语义边不受影响。"""
    store = _mk_store(tmp_path)
    ex = KnowledgeGraphExtractor(store)
    ex.ai_client = _FakeAI(json.dumps({
        "entities": [
            {"name": "北京", "type": "location", "aliases": []},
            {"name": "上海", "type": "location", "aliases": []},
            {"name": "广州", "type": "location", "aliases": []},
        ],
        "relations": [
            {"source": "北京", "target": "上海", "relation": "遥遥相望"},
            {"source": "北京", "target": "广州", "relation": "part_of"},
        ],
    }))
    result = ex._extract_with_llm("城市关系测试", source_id="s1")
    assert ex._stats.get("relation_dropped") == 1
    types = [r.get("relation_type") for r in store.get_all_relations()]
    assert types == [REL_PART_OF], f"垃圾谓词边不应入库: {types}"
    assert len(result.get("relations") or []) == 1


# ---------------------------------------------------------------- 遗留迁移


def test_incremental_migration_of_legacy_edges(tmp_path) -> None:
    """构造旧版污染数据（容器+mentioned_in 边），再次抽取 → 就地迁移、幂等。"""
    store = _mk_store(tmp_path)
    # 旧管线产物：实体 e1 + 容器 mem_conv + 出处边
    store.add_entity("e1", "量子计算", "concept")
    store.add_entity(
        "mem_conv0001", "记忆:conv-0001", "concept",
        properties={"source_id": "conv-0001", "is_memory": True},
    )
    store.add_relation("e1", "mem_conv0001", REL_MENTIONED_IN)
    # 一条真语义边挂在容器上——容器不应被误删
    store.add_entity("e2", "量子霸权", "concept")
    store.add_relation("e2", "e1", REL_PART_OF)
    store.save()

    # 触发一次抽取（正则通道），碰到 e1 → 迁移其遗留出处边
    ex = KnowledgeGraphExtractor(store)
    ex._purge_stale_provenance_edges(["e1"])
    store.save()

    rels = store.get_all_relations()
    assert all(r.get("relation_type") != REL_MENTIONED_IN for r in rels), (
        f"遗留出处边未迁出: {rels}"
    )
    assert store.get_entity("e1")["properties"]["sources"] == ["conv-0001"]
    assert store.get_entity("mem_conv0001") is None, "孤儿容器应删除"
    assert store.get_entity("e2") is not None and len(rels) == 1

    # 幂等：再跑一次无变化
    assert ex._purge_stale_provenance_edges(["e1"]) == 0
    assert store.get_entity("e1")["properties"]["sources"] == ["conv-0001"]


def test_prompt_context_excludes_provenance_and_weak_edges(tmp_path) -> None:
    """A2：提示词注入只取强语义谓词——出处边与弱 related_to 不进 prompt。"""
    store = _mk_store(tmp_path)
    store.add_entity("e1", "深度学习", "concept")
    store.add_entity("e2", "神经网络", "concept")
    store.add_entity(
        "mem_x1", "记忆:chat-1", "concept",
        properties={"source_id": "chat-1", "is_memory": True},
    )
    store.add_relation("e1", "e2", REL_PART_OF)
    store.add_relation("e1", "mem_x1", REL_MENTIONED_IN)
    store.add_relation("e1", "e2", REL_RELATED_TO)
    store.save()

    ex = KnowledgeGraphExtractor(store)
    ctx = ex.query_for_prompt_context("深度学习怎么样")
    assert "part_of" in ctx and "神经网络" in ctx, f"强语义边应保留:\n{ctx}"
    assert "mentioned_in" not in ctx, "A2 违规：出处边注入 prompt"
    assert "related_to" not in ctx, "A2 违规：弱边注入 prompt"


def test_migration_preserves_unrelated_mentioned_in(tmp_path) -> None:
    """迁移只动 ids 相关边——其他实体上的遗留边保留（碰到谁迁谁）。"""
    store = _mk_store(tmp_path)
    store.add_entity("e1", "甲", "concept")
    store.add_entity("e2", "乙", "concept")
    store.add_entity("mem_c2", "记忆:c2", "concept",
                     properties={"source_id": "c2", "is_memory": True})
    store.add_relation("e1", "mem_c2", REL_MENTIONED_IN)
    store.add_relation("e2", "mem_c2", REL_MENTIONED_IN)
    store.save()

    ex = KnowledgeGraphExtractor(store)
    assert ex._purge_stale_provenance_edges(["e1"]) == 1
    left = [r for r in store.get_all_relations() if r.get("relation_type") == REL_MENTIONED_IN]
    assert len(left) == 1 and left[0]["source_id"] == "e2", (
        f"e2 的遗留边不应被顺带删除: {left}"
    )
    # 容器还有 e2 的边 → 不能被删
    assert store.get_entity("mem_c2") is not None
    # get_relations 索引仍可见该边（索引剔除按对象身份）
    assert any(
        r.get("relation_type") == REL_MENTIONED_IN
        for r in store.get_relations("mem_c2", direction="in")
    )
