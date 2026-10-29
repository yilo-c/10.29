"""graph_extractor 提取形状与失败路径的回归测试。

历史（debt-v2 A-2）：LLM 偶发返回 JSON 数组（list）时，``data.get(...)`` 抛
AttributeError 被宽 except 兜底，刷 traceback 白耗一次调用。当时加了
``isinstance(data, dict)`` 守卫，并把「失败回退正则模式提取」当作期望行为
（``via == "fallback"``）。

2026-09-16 批3 契约变更（本文件随代码同批修改，理由如下）：
「失败静默回退正则」被判定为 **X1 静默降级缺陷**（审计
deliverables/knowledge-graph-pipeline-audit-2026-09-16.md W1/X1 与
md_parser.py:178-180 的既有判定基准一致）——正则通道对聊天/摘要文本会产出
大量句子碎片噪声实体，"产垃圾"比"空手而归"更有害；且回退路径让上层把一次
LLM 失败误认为一次成功提取，统计与降级痕迹全部丢失。
新契约：LLM 失败/形状非法/无内核 → ``via == "skipped"`` + ``error``，
**不再**调用 ``_extract_with_patterns``。旧断言 ``via == "fallback"`` 锁死的
正是这个被推翻的行为，故改为断言 skipped 且 patterns 通道零调用。
"""

from __future__ import annotations

from knowledge.graph_extractor import KnowledgeGraphExtractor


class _FakeAI:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete_text(self, messages, **kwargs):  # noqa: ANN001
        return self._reply


class _StoreStub:
    """最小 store 桩：dict 形状正常路径所需的方法。"""

    def find_by_name(self, name):  # noqa: ANN001
        return None

    def add_entity(self, *args, **kwargs):  # noqa: ANN001
        return "e1"

    def get_entity(self, entity_id):  # noqa: ANN001
        return None

    def add_relation(self, *args, **kwargs):  # noqa: ANN001
        pass

    def get_all_entities(self):  # noqa: ANN001
        return []

    def save(self):  # noqa: ANN001
        pass


def _exploding_patterns(extractor: KnowledgeGraphExtractor):
    """把正则通道替换为「被调用即失败」，证明 LLM 路径不再回落。"""

    def _boom(text, sid="", source_kind=""):  # noqa: ARG001
        raise AssertionError("LLM 失败路径不得回落正则通道（X1 契约）")

    extractor._extract_with_patterns = _boom  # type: ignore[method-assign]


def test_llm_array_response_skipped_not_fallback() -> None:
    """LLM 返回 JSON 数组（list 形状）→ via=skipped + error，不回落正则。"""
    extractor = KnowledgeGraphExtractor(graph_store=object())
    _exploding_patterns(extractor)
    extractor.ai_client = _FakeAI('[{"e":1},{"e":2}]')

    result = extractor._extract_with_llm("测试文本", "src1")
    assert result.get("via") == "skipped"
    assert "non_object_json" in str(result.get("error", ""))


def test_llm_dict_shape_empty_parsed_ok() -> None:
    """LLM 返回合法对象（空 entities/relations）→ 正常产出，不回退。"""
    extractor = KnowledgeGraphExtractor(graph_store=_StoreStub())
    extractor.ai_client = _FakeAI('{"entities": [], "relations": []}')

    result = extractor._extract_with_llm("文本", "src")
    assert isinstance(result, dict)
    assert result["entities"] == []
    assert result["relations"] == []
    assert result.get("via") == "llm"
    assert extractor._stats["llm_extractions"] == 1


def test_llm_invalid_json_skipped() -> None:
    """LLM 返回非 JSON → via=skipped + error，不回落正则。"""
    extractor = KnowledgeGraphExtractor(graph_store=object())
    _exploding_patterns(extractor)
    extractor.ai_client = _FakeAI("这不是 JSON")

    result = extractor._extract_with_llm("文本", "src")
    assert result.get("via") == "skipped"
    assert result.get("error")


def test_extract_from_text_without_ai_client_skipped() -> None:
    """use_llm=True 但无 ai_client → skipped（不静默改走正则）。"""
    extractor = KnowledgeGraphExtractor(graph_store=object(), ai_client=None)
    result = extractor.extract_from_text("任何文本", source_id="s", use_llm=True)
    assert result.get("via") == "skipped"
    assert result.get("error") == "no_ai_client"


# ── P4-KG-DETAIL（2026-10-15）：实体详情一次性提取 ──────────────────
#
# 根因背景：写侧 prompt 从未要求 LLM 产出 detail，739 个存量实体 0 个有
# 详情，详情面板「打开也没东西看」。本组测试锁死三个契约：
#   1. 新实体：detail 随 add_entity 的 properties 落库；
#   2. detail 为空：properties 不下发（宁缺毋滥，不写空串占位）；
#   3. 已存在实体：detail 经 add_entity(properties=...) 合并更新（不改名）。


class _RecordingStore:
    """记录 add_entity 调用的 store 桩。"""

    def __init__(self, existing: dict | None = None) -> None:
        self._existing = existing or {}
        self.calls: list[dict] = []

    def find_by_name(self, name):  # noqa: ANN001
        hit = self._existing.get(name)
        return [hit] if hit else None

    def add_entity(self, entity_id, name, entity_type="unknown", aliases=None, properties=None):  # noqa: ANN001
        self.calls.append(
            {
                "entity_id": entity_id,
                "name": name,
                "type": entity_type,
                "aliases": aliases,
                "properties": properties,
            }
        )
        return entity_id

    def get_entity(self, entity_id):  # noqa: ANN001
        return None

    def add_relation(self, *args, **kwargs):  # noqa: ANN001
        pass

    def get_all_entities(self):  # noqa: ANN001
        return []

    def save(self):  # noqa: ANN001
        pass


def test_llm_detail_written_to_new_entity() -> None:
    """LLM 返回 detail → 新实体落库时 properties.detail 同步写入。"""
    store = _RecordingStore()
    extractor = KnowledgeGraphExtractor(graph_store=store)
    extractor.ai_client = _FakeAI(
        '{"entities": [{"name": "蓝鲸计划", "type": "project", "aliases": [], "detail": "一个数据平台重构项目"}], "relations": []}'
    )

    result = extractor._extract_with_llm("我们启动了蓝鲸计划，要做数据平台重构", "note:1")
    assert result.get("via") == "llm"
    assert len(store.calls) == 1
    assert store.calls[0]["properties"] == {"detail": "一个数据平台重构项目"}


def test_llm_detail_empty_not_written() -> None:
    """detail 空串 → properties 不下发（不写空串占位，宁缺毋滥）。"""
    store = _RecordingStore()
    extractor = KnowledgeGraphExtractor(graph_store=store)
    extractor.ai_client = _FakeAI('{"entities": [{"name": "李四", "type": "person", "aliases": [], "detail": ""}], "relations": []}')

    extractor._extract_with_llm("李四说要去上海市出差", "note:1")
    assert len(store.calls) == 1
    assert store.calls[0]["properties"] is None


def test_llm_detail_updates_existing_entity() -> None:
    """已存在实体 → detail 经 add_entity(properties=...) 合并更新，不改名。"""
    store = _RecordingStore(existing={"蓝鲸计划": {"id": "ent_pro_existing"}})
    extractor = KnowledgeGraphExtractor(graph_store=store)
    extractor.ai_client = _FakeAI(
        '{"entities": [{"name": "蓝鲸计划", "type": "project", "aliases": ["蓝鲸"], '
        '"detail": "数据平台重构项目，本季度重点是迁移"}], "relations": []}'
    )

    extractor._extract_with_llm("蓝鲸计划又叫蓝鲸，本季度重点是迁移", "note:2")
    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["entity_id"] == "ent_pro_existing"
    assert call["properties"] == {"detail": "数据平台重构项目，本季度重点是迁移"}
    assert call["aliases"] == ["蓝鲸"]
