"""P4-KG-ORIGIN（2026-09-26）· 出处命名空间统一 + 级联清理三态匹配回归。

要治的两条实测病：
1. **出处无域**：6 条写入通道往 `properties.sources` 写裸 entry_id（生产图谱实测
   445 条裸串 / 524 条 `deepseek:`），于是「这个节点来自笔记还是记忆」不可判定，
   详情面板无法给出可跳转来源。
2. **级联清理恒 no-op**：`remove_by_source()` 只读 `properties.source_id`，而批3 W1
   之后没有任何写入端再写这个键（生产 739 实体里 source_id = **0**）→ 笔记/任务/
   收件箱/资讯删除后图谱实体永久残留成幽灵。

并要求一条反例：带**别的域**命名空间的实体，不能因为裸 id 撞号被误删。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from knowledge.graph_extractor import KnowledgeGraphExtractor  # noqa: E402
from knowledge.graph_store import KnowledgeGraphStore, format_origin, split_origin  # noqa: E402


class _LLM:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload, ensure_ascii=False)

    def complete_text(self, messages, temperature=0.1, max_tokens=1000, **kwargs):  # noqa: ANN001, D102
        return self._payload


def _payload(name: str) -> dict:
    return {"entities": [{"name": name, "type": "concept"}], "relations": []}


# ── 1. 串的规范形 ─────────────────────────────────────────


def test_split_origin_recognizes_all_three_generations() -> None:
    assert split_origin("note:42") == ("note", "42")
    assert split_origin("deepseek:conv-9") == ("deepseek", "conv-9")
    assert split_origin("kimi_1712345678") == ("kimi", "1712345678")
    assert split_origin("bare-id-7") == ("unknown", "bare-id-7")
    assert split_origin("") == ("unknown", "")


def test_format_origin_does_not_invent_a_namespace() -> None:
    assert format_origin("note", 42) == "note:42"
    assert format_origin("", "42") == "42"
    assert format_origin("unknown", "42") == "42"
    assert format_origin("note", "") == ""


# ── 2. add_entity_source 双写与去重 ───────────────────────


def test_add_entity_source_writes_structured_origins_and_mirrors_sources(tmp_path) -> None:
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("e1", "某实体", "concept")

    assert store.add_entity_source("e1", "42", kind="note") is True

    entity = store.get_entity("e1")
    assert entity is not None
    props = entity["properties"]
    assert props["origins"] == [{"kind": "note", "id": "42"}], props
    assert props["sources"] == ["note:42"], f"sources 需镜像规范串，兼容旧读端: {props!r}"


def test_add_entity_source_infers_kind_from_prefixed_string(tmp_path) -> None:
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("e1", "某实体", "concept")

    store.add_entity_source("e1", "deepseek:conv-9")

    entity = store.get_entity("e1")
    assert entity is not None
    assert entity["properties"]["origins"] == [{"kind": "deepseek", "id": "conv-9"}]


def test_add_entity_source_dedupes_by_kind_and_id(tmp_path) -> None:
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("e1", "某实体", "concept")

    first = store.add_entity_source("e1", "note:42")
    second = store.add_entity_source("e1", "note:42")
    # 同 id 不同域是两条真实出处（笔记 42 与 记忆 42），不得互相吞掉
    third = store.add_entity_source("e1", "42", kind="memory")

    assert (first, second, third) == (True, False, True)
    merged_entity = store.get_entity("e1")
    assert merged_entity is not None
    origins = merged_entity["properties"]["origins"]
    assert {(o["kind"], o["id"]) for o in origins} == {("note", "42"), ("memory", "42")}, origins


# ── 3. remove_by_source 三态匹配 ─────────────────────────


def test_remove_by_source_matches_structured_origins(tmp_path) -> None:
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("e1", "笔记派生实体", "concept")
    store.add_entity_source("e1", "42", kind="note")

    assert store.remove_by_source("note", 42) == 1
    assert store.get_entity("e1") is None


def test_remove_by_source_still_matches_legacy_sources_list(tmp_path) -> None:
    """旧代数据只有 sources 字符串列表（无 origins），也必须能清掉。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("e2", "旧实体", "concept", properties={"sources": ["note:42"]})

    assert store.remove_by_source("note", "42") == 1, "W1 后旧数据不得成为清不掉的幽灵"


def test_remove_by_source_still_matches_legacy_source_id_field(tmp_path) -> None:
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("e3", "外部内容实体", "external_content", properties={"source_id": "art-9"})

    assert store.remove_by_source("reading_article", "art-9") == 1


def test_bare_id_fallback_only_for_unnamed_entities(tmp_path) -> None:
    """反例：出处已标明是 memory 的裸 id，不能被当成 note 删掉。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("mem", "记忆派生实体", "concept")
    store.add_entity_source("mem", "42", kind="memory")

    assert store.remove_by_source("note", "42") == 0, "跨域误删：note:42 不该吃掉 memory:42"
    assert store.get_entity("mem") is not None
    assert store.remove_by_source("memory", "42") == 1


def test_legacy_pure_bare_source_is_still_collectable(tmp_path) -> None:
    """真正的裸串历史数据（无域可判）保留旧的按 id 兜底能力。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("legacy", "老数据实体", "concept", properties={"sources": ["42"]})

    assert store.remove_by_source("memory", "42") == 1


def test_remove_by_source_rejects_empty_args(tmp_path) -> None:
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("e", "x", "concept")
    store.add_entity_source("e", "1", kind="note")

    assert store.remove_by_source("", "1") == 0
    assert store.remove_by_source("note", "") == 0
    assert store.get_entity("e") is not None


# ── 4. 生产者-消费者一致性（专治"手搓 source_id 的假绿测试"）──────


def test_producer_path_writes_origin_that_consumer_can_delete(tmp_path) -> None:
    """走真实记忆回灌通道写入，再由级联清理按域+id 删除 —— 两端必须同口径。

    旧测试直接 `add_entity(..., properties={"source_id": "note:42"})` 造数据，
    于是"测试绿而生产写入端早就不写这个键"的断裂被永久掩盖（本文件第 3 条
    remove_by_source 三态匹配就是被这条逼出来的）。
    """
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    extractor = KnowledgeGraphExtractor(store, ai_client=_LLM(_payload("蓝鲸计划")))

    extractor.extract_from_memory({"id": "entry-77", "content": "王小明启动了蓝鲸计划"}, use_llm=True)

    entity = next(e for e in store.get_all_entities() if e["name"] == "蓝鲸计划")
    origins = entity["properties"]["origins"]
    assert origins == [{"kind": "memory", "id": "entry-77"}], f"回灌通道要带域，实为: {origins!r}"

    assert store.remove_by_source("memory", "entry-77") == 1, "同一通道写入的出处必须能被同一口径清掉"
    assert store.get_entity(entity["id"]) is None


def test_info_collect_channel_tags_info_namespace(tmp_path) -> None:
    """info.collect 显式走正则通道（结构化短文本），同样要带域。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    extractor = KnowledgeGraphExtractor(store, ai_client=None)

    extractor.extract_from_memory({"id": "digest-5", "content": "下周去上海市出差"}, use_llm=False, source_kind="info")

    names = [e["name"] for e in store.get_all_entities()]
    assert "上海市" in names, names
    entity = next(e for e in store.get_all_entities() if e["name"] == "上海市")
    assert entity["properties"]["origins"] == [{"kind": "info", "id": "digest-5"}], entity
    assert store.remove_by_source("info", "digest-5") == 1


def test_kimi_namespace_is_carried_end_to_end(tmp_path) -> None:
    """Kimi 旧写法 `kimi_<ts>`（下划线）也要被归一成 kind=kimi。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    extractor = KnowledgeGraphExtractor(store, ai_client=_LLM(_payload("Kimi 会话实体")))

    extractor.extract_from_text("原文", source_id="kimi_1712345678", use_llm=True)

    entity = next(e for e in store.get_all_entities() if e["name"] == "Kimi 会话实体")
    assert entity["properties"]["origins"] == [{"kind": "kimi", "id": "1712345678"}], entity
    assert store.remove_by_source("kimi", "1712345678") == 1
