"""KG-CH（2026-09-21）· 知识图谱写入通道契约测试。

背景（承接 2026-09-16 批3 X1 的未完成部分）：X1 只堵住"LLM 失败静默回落正则"，
**没有动 ``extract_from_text`` / ``extract_from_memory`` 的默认值**，于是所有
省略 ``use_llm`` 的调用点（生产上即 ``MemoryExtractor.extract`` 的 auto_write
回灌，memory/extractor/_core.py:253）仍然把聊天/记忆文本喂给正则通道，
产出「思忆集重构项目启动会开得很顺利」式的句子碎片噪声实体——正是抽取器
模块 docstring 明令禁止的行为。

本模块把契约固化为机器断言：
1. 省略 ``use_llm`` = LLM 通道（默认值即契约，不再依赖调用方自觉）；
2. 无 AI 内核时**宁缺毋滥**：不产实体，且**绝不进正则通道**；
3. 正则通道只在接受显式 ``use_llm=False`` 的结构化短文本时工作（不回归）；
4. 记忆抽取的 auto_write 回灌确实以 LLM 通道调用抽取器（接线断言）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from knowledge.graph_extractor import KnowledgeGraphExtractor  # noqa: E402
from knowledge.graph_store import KnowledgeGraphStore  # noqa: E402

# 聊天体文本：正则通道会从中切出「张三说」「项目启动会」等碎片噪声
CHAT_TEXT = "张三说：「思忆集重构项目启动会开得很顺利」，地点在上海市，我觉得可以"


class _LLM:
    """最小抽取内核：complete_text 返回固定 entities/relations JSON。"""

    def __init__(self, payload: dict | None = None) -> None:
        self._payload = json.dumps(payload or {"entities": [], "relations": []}, ensure_ascii=False)
        self.prompts: list[str] = []

    def complete_text(self, messages, temperature=0.1, max_tokens=1000, **kwargs):  # noqa: ANN001, D102
        self.prompts.append(str(messages[-1]["content"]) if messages else "")
        return self._payload


def _entity_names(store: KnowledgeGraphStore) -> list[str]:
    return sorted(str(e.get("name", "")) for e in store.get_all_entities())


# ── 1/2. 默认即 LLM 通道；无内核不得回落到正则 ──────────────────────────


def test_default_channel_is_llm_not_regex(tmp_path) -> None:
    """省略 use_llm 必须走 LLM：无内核时零实体（旧行为会入库正则噪声）。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    ex = KnowledgeGraphExtractor(store, ai_client=None)

    result = ex.extract_from_text(CHAT_TEXT, source_id="note:1")

    assert result.get("via") == "skipped", f"无内核 + 默认参数应跳过而非走正则: {result!r}"
    assert result.get("error") == "no_ai_client", f"应留痕 no_ai_client: {result!r}"
    assert _entity_names(store) == [], f"默认通道不得产出正则噪声实体: {_entity_names(store)!r}"


def test_default_never_enters_regex_channel(tmp_path, monkeypatch) -> None:
    """结构性断言：默认调用不得触达 _extract_with_patterns（回落链已封死）。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    ex = KnowledgeGraphExtractor(store, ai_client=None)

    def _boom(*args, **kwargs):
        raise AssertionError("默认通道不得调用正则提取")

    monkeypatch.setattr(ex, "_extract_with_patterns", _boom)
    ex.extract_from_text(CHAT_TEXT, source_id="note:1")
    ex.extract_from_memory({"entry_id": "note:1", "content": CHAT_TEXT})


def test_default_uses_llm_when_kernel_present(tmp_path) -> None:
    """有内核时默认参数即走 LLM，实体类型由 LLM 决定而非正则猜测。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    llm = _LLM(
        {
            "entities": [{"name": "张三", "type": "person"}, {"name": "思忆集重构项目", "type": "project"}],
            "relations": [{"source": "张三", "target": "思忆集重构项目", "relation": "part_of"}],
        }
    )
    ex = KnowledgeGraphExtractor(store, ai_client=llm)

    result = ex.extract_from_text(CHAT_TEXT, source_id="note:1")

    assert result.get("via") == "llm", f"默认应为 LLM 通道: {result!r}"
    assert llm.prompts, "LLM 内核应被调用"
    assert set(_entity_names(store)) == {"张三", "思忆集重构项目"}, _entity_names(store)
    # 出处走 properties.sources（W1 契约），不再建 mentioned_in 边
    kinds = {r.get("relation_type") for r in store.get_all_relations()}
    assert kinds == {"part_of"}, f"关系表只应存语义边: {kinds!r}"


# ── 3. 正则通道降级为显式选项（info.collect 一类结构化短文本）──────────


def test_regex_channel_still_available_when_explicitly_requested(tmp_path) -> None:
    """显式 use_llm=False 仍走正则：不得把 info.collect 通道一并改死。"""
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    ex = KnowledgeGraphExtractor(store, ai_client=None)

    result = ex.extract_from_text("下周去上海市出差", source_id="info:1", use_llm=False)

    assert result.get("via") == "patterns", f"显式请求应走正则: {result!r}"
    assert "上海市" in _entity_names(store), _entity_names(store)


# ── 4. 记忆抽取回灌的接线断言（缺陷原始现场）────────────────────────


class _RecordingKG:
    """记录 MemoryExtractor 回灌时实际传入的参数。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.store = object()

    def extract_from_memory(self, memory_entry, **kwargs):  # noqa: ANN001, D102
        self.calls.append({"entry": memory_entry, **kwargs})
        return {"entities": [], "relations": [], "via": "skipped"}


def _auto_write_candidate():
    """构造一条 confidence ≥ AUTO_WRITE_THRESHOLD 的候选（0.9 ≥ 0.8）。"""
    from memory.types import MemoryCandidate  # 候选模型真源

    return MemoryCandidate(
        type="project",
        content=CHAT_TEXT,
        confidence=0.9,
        source="user",
        tags=["KG-CH"],
        reason="默认通道断言用候选",
    )


def test_memory_auto_write_reinject_uses_llm_channel(tmp_path, monkeypatch) -> None:
    """memory/extractor/_core.py 的 auto_write 回灌必须以 LLM 通道调用抽取器。

    这是 KG-CH 缺陷的原始现场：该调用点省略 use_llm，默认值即契约。
    候选生成（_llm_extract）被打桩替换，只观察回灌接线，不驱动真实 LLM。
    """
    from data.local_storage import LocalDataStore
    from memory.extractor import MemoryExtractor

    kg = _RecordingKG()
    store = LocalDataStore(base_dir=str(tmp_path))
    extractor = MemoryExtractor(
        ai_client=object(),  # 非 None 即可：extract() 的前置守卫
        data_store=store,
        memory_index_service=None,
        knowledge_graph_extractor=kg,
    )
    monkeypatch.setattr(extractor, "_llm_extract", lambda *a, **k: [_auto_write_candidate()])

    result = extractor.extract(
        [{"role": "user", "content": CHAT_TEXT}],
        conversation_id="c1",
        existing_memories=[],
    )

    assert result.auto_write, "confidence 0.9 应进入 auto_write"
    assert kg.calls, "auto_write 应回灌知识图谱"
    for call in kg.calls:
        assert call.get("use_llm") is True, f"回灌必须走 LLM 通道（默认值即契约），实收: {call!r}"
