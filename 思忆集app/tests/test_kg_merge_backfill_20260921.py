"""KG-MERGE / KG-BACKFILL / KG-CAP（2026-09-21）· 知识图谱三项新能力回归。

对应本轮报告里被列为"未做"的三项：
* **P3 实体合并**（`KnowledgeGraphStore.merge_entities` + `knowledge.entity.merge` RPC）：
  图谱里同一事物常以多节点共存，`find_by_name` 默认是规范化精确匹配（PD-5b）不会
  自动折叠，此前全仓也无归并入口 —— 只有 `remove_entity` 一删了之（连带丢边）。
* **P1-2 关系回填**（`knowledge.backfill` RPC）：通道改对（KG-CH）之后，存量图谱
  仍缺语义关系（实测 support/oppose=0）。按本仓"核心功能 opt-in 渐进式发布"规范，
  它**只能由用户显式调用**，不挂 cron、不自动跑，且默认只处理 limit 条。
* **P3 规模上限**（`knowledge.graph` 的 `max_entities`）：传输层此前无任何上限。

诚实性断言照抄同域既有教训：未命中如实 no-op、内存改了但落盘失败不得报成功、
无 AI 内核不得拿正则冒充回填结果。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._inbox_knowledge import register_inbox_knowledge  # noqa: E402
from backend.method_registry import MethodRegistry  # noqa: E402
from data.local_storage import LocalDataStore  # noqa: E402
from knowledge.graph_extractor import KnowledgeGraphExtractor  # noqa: E402
from knowledge.graph_store import KnowledgeGraphStore  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _LLM:
    """假抽取内核：complete_text 返回固定 entities/relations（LLM 通道契约）。"""

    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload, ensure_ascii=False)
        self.calls = 0

    def complete_text(self, messages, temperature=0.1, max_tokens=1000, **kwargs):  # noqa: ANN001, D102
        self.calls += 1
        return self._payload


# ══════════════════════════════════════════════════════════════
# A. store 层合并不变量
# ══════════════════════════════════════════════════════════════


def _store_with_dupes(tmp_path) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("ent_a", "量子计算", "concept")
    store.add_entity("ent_b", "量子", "concept", aliases=["qubit 群"])
    store.add_entity("ent_c", "蓝鲸计划", "project")
    # a→c 与 b→c 将在合并后成为重复边；b→a 将变成自环
    store.add_relation("ent_a", "ent_c", "part_of", confidence=0.6)
    store.add_relation("ent_b", "ent_c", "part_of", confidence=0.9)
    store.add_relation("ent_b", "ent_a", "related_to")
    store.add_entity_source("ent_a", "note:1")
    store.add_entity_source("ent_b", "note:2")
    store.add_entity_source("ent_b", "note:1")  # 与 a 重合，合并后不得重复
    return store


def test_merge_repoints_edges_and_drops_self_loops(tmp_path) -> None:
    store = _store_with_dupes(tmp_path)

    result = store.merge_entities("ent_b", "ent_a")

    assert result["merged"] is True, result
    assert store.get_entity("ent_b") is None, "source 实体必须消失"
    assert store.get_entity("ent_a") is not None, "target 必须保留"
    # b→a 变自环被丢弃；b→c 转指为 a→c 并与既有 a→c 归一
    rels = store.get_all_relations()
    assert len(rels) == 1, f"应只剩一条 a→c 边: {rels!r}"
    assert rels[0]["source_id"] == "ent_a" and rels[0]["target_id"] == "ent_c"
    assert rels[0]["confidence"] == 0.9, f"重复边归一时 confidence 应取大: {rels[0]!r}"
    assert result["self_loops_dropped"] == 1, result
    assert result["duplicates_merged"] == 1, result


def test_merge_preserves_target_identity_and_folds_names(tmp_path) -> None:
    store = _store_with_dupes(tmp_path)

    store.merge_entities("ent_b", "ent_a")

    target = store.get_entity("ent_a") or {}
    assert target.get("name") == "量子计算", f"target 名称不得被静默改写: {target!r}"
    assert target.get("type") == "concept", f"target 类型不得被改写: {target!r}"
    assert "量子" in (target.get("aliases") or []), f"source 主名应降为别名: {target!r}"
    assert "qubit 群" in (target.get("aliases") or []), f"source 别名应并入: {target!r}"
    # 合并的真实目的：两个名字都能召回同一个实体
    assert [e.get("id") for e in store.find_by_name("量子")] == ["ent_a"], "旧名应命中合并后的 target"
    assert [e.get("id") for e in store.find_by_name("量子计算")] == ["ent_a"]
    # 名称索引里的 source 名必须不再指向幽灵实体
    assert store._name_index.get("量子") == "ent_a", f"旧名应指向合并后的 target: {store._name_index!r}"


def test_merge_unions_provenance_without_duplicates(tmp_path) -> None:
    store = _store_with_dupes(tmp_path)

    result = store.merge_entities("ent_b", "ent_a")

    sources = (store.get_entity("ent_a") or {}).get("properties", {}).get("sources")
    assert sorted(sources or []) == ["note:1", "note:2"], f"出处需去重合并: {sources!r}"
    assert result["sources_merged"] == 1, result


def test_merge_is_persisted_and_survives_reload(tmp_path) -> None:
    store = _store_with_dupes(tmp_path)
    store.merge_entities("ent_b", "ent_a")
    store.save()

    reloaded = KnowledgeGraphStore(path=tmp_path / "kg.json")
    assert reloaded.get_entity("ent_b") is None, "合并未落盘：重开后 source 又活了"
    assert [e.get("id") for e in reloaded.find_by_name("量子")] == ["ent_a"]


def test_merge_rejects_bad_input_without_touching_state(tmp_path) -> None:
    store = _store_with_dupes(tmp_path)
    before_entities = {e["id"] for e in store.get_all_entities()}
    before_relations = len(store.get_all_relations())

    assert store.merge_entities("ent_a", "ent_a").get("error") == "source and target are the same entity"
    assert store.merge_entities("ent_x", "ent_a").get("merged") is False
    assert store.merge_entities("", "ent_a").get("merged") is False
    assert store.merge_entities("ent_a", "ent_y").get("merged") is False

    assert {e["id"] for e in store.get_all_entities()} == before_entities, "失败路径不得改状态"
    assert len(store.get_all_relations()) == before_relations, "失败路径不得丢边"


# ══════════════════════════════════════════════════════════════
# B. knowledge.entity.merge RPC
# ══════════════════════════════════════════════════════════════


def _registry(tmp_path, kg: KnowledgeGraphStore, extractor=None):
    ctx = SimpleNamespace(
        knowledge_graph_store=kg,
        knowledge_graph_extractor=extractor,
        local_data_store=SimpleNamespace(base_dir=str(tmp_path)),
    )
    reg = MethodRegistry()
    register_inbox_knowledge(reg, ctx)
    return reg, ctx


def test_rpc_entity_merge_persists(tmp_path) -> None:
    kg = _store_with_dupes(tmp_path)
    reg, _ctx = _registry(tmp_path, kg)

    res = _run(reg.dispatch("knowledge.entity.merge", {"entity_id": "ent_b", "into_entity_id": "ent_a"}))

    assert res.get("success") is True and res.get("merged") is True, res
    assert kg.get_entity("ent_b") is None
    assert json.loads((tmp_path / "kg.json").read_text(encoding="utf-8"))["entities"].get("ent_b") is None, "只改内存则重启复活"


def test_rpc_entity_merge_reports_save_failure_honestly(tmp_path, monkeypatch) -> None:
    kg = _store_with_dupes(tmp_path)
    reg, _ctx = _registry(tmp_path, kg)

    def _boom() -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(kg, "save", _boom)

    res = _run(reg.dispatch("knowledge.entity.merge", {"entity_id": "ent_b", "into_entity_id": "ent_a"}))

    assert res.get("merged") is True, f"内存确实合并了: {res!r}"
    assert res.get("success") is False and "save failed" in str(res.get("error")), f"落盘失败必须如实上报: {res!r}"


def test_rpc_entity_merge_not_found_is_not_success(tmp_path) -> None:
    kg = _store_with_dupes(tmp_path)
    reg, _ctx = _registry(tmp_path, kg)

    res = _run(reg.dispatch("knowledge.entity.merge", {"entity_id": "ghost", "into_entity_id": "ent_a"}))

    assert res.get("success") is False and res.get("merged") is False, res


def test_rpc_entity_merge_requires_both_ids(tmp_path) -> None:
    kg = _store_with_dupes(tmp_path)
    reg, _ctx = _registry(tmp_path, kg)

    res = _run(reg.dispatch("knowledge.entity.merge", {"entity_id": "ent_a"}))

    assert res.get("success") is False and "required" in str(res.get("error")), res


# ══════════════════════════════════════════════════════════════
# C. knowledge.backfill（opt-in 关系回填）
# ══════════════════════════════════════════════════════════════


def _entries(n: int) -> list[dict]:
    return [
        {
            "id": f"card-{i}",
            "entity_type": "knowledge.card",
            "title": f"卡片{i}",
            "content": f"第{i}条：王小明负责蓝鲸计划",
            "created_at": f"2026-09-{20 - i:02d}",
        }
        for i in range(1, n + 1)
    ]


_PAYLOAD = {
    "entities": [{"name": "王小明", "type": "person"}, {"name": "蓝鲸计划", "type": "project"}],
    "relations": [{"source": "王小明", "target": "蓝鲸计划", "relation": "part_of"}],
}


def _backfill_ctx(tmp_path, extractor, kg, store):
    ctx = SimpleNamespace(
        knowledge_graph_store=kg,
        knowledge_graph_extractor=extractor,
        local_data_store=store,
    )
    reg = MethodRegistry()
    register_inbox_knowledge(reg, ctx)
    return reg


def test_backfill_dry_run_makes_no_llm_calls(tmp_path) -> None:
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("structured_memory_entries", _entries(3))
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    llm = _LLM(_PAYLOAD)
    extractor = KnowledgeGraphExtractor(kg, ai_client=llm)
    reg = _backfill_ctx(tmp_path, extractor, kg, store)

    res = _run(reg.dispatch("knowledge.backfill", {"dry_run": True, "limit": 2}))

    assert res.get("success") is True and res.get("dry_run") is True, res
    assert llm.calls == 0, "dry_run 不得产生任何 LLM 调用（花钱前先看清单）"
    assert res["candidates"] == 3 and res["would_process"] == 2 and res["remaining"] == 1, res
    assert res["offset"] == 0 and res["next_offset"] == 2, res
    assert kg.get_all_entities() == [], "dry_run 不得写图"


def test_backfill_writes_semantic_relations(tmp_path) -> None:
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("structured_memory_entries", _entries(2))
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    llm = _LLM(_PAYLOAD)
    extractor = KnowledgeGraphExtractor(kg, ai_client=llm)
    reg = _backfill_ctx(tmp_path, extractor, kg, store)

    res = _run(reg.dispatch("knowledge.backfill", {"limit": 2}))

    assert res.get("success") is True, res
    assert llm.calls == 2, f"每条候选各一次 LLM 抽取: {llm.calls}"
    assert res["attempted"] == 2 and res["by_via"].get("llm") == 2, res
    assert res["new_entities"] == 2, f"应新增 2 个实体: {res!r}"
    assert res["new_relations"] == 1, f"应新增 1 条语义边: {res!r}"
    kinds = {r["relation_type"] for r in kg.get_all_relations()}
    assert kinds == {"part_of"}, f"回填出的应是语义边: {kinds!r}"
    # 出处按 W1 契约写进 properties.sources（不再建 mentioned_in 边）；
    # P4-KG-ORIGIN（2026-09-26）后一定带域前缀，本通道默认归 memory 族
    # （旧断言锁的是裸 entry_id，那正是“出处无域→无法跳转与级联清理”的病形）。
    all_sources = {str(s) for e in kg.get_all_entities() for s in ((e.get("properties") or {}).get("sources") or [])}
    assert {"memory:card-1", "memory:card-2"} <= all_sources, f"出处应带命名空间: {all_sources!r}"


class _PromptAwareLLM:
    """从 prompt 里回提条目标记，用于证明 offset 游标真的换了一批。"""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def complete_text(self, messages, temperature=0.1, max_tokens=1000, **kwargs):  # noqa: ANN001, D102
        import re as _re

        text = str(messages[-1]["content"]) if messages else ""
        match = _re.search(r"第(\d+)条", text)
        tag = match.group(1) if match else "?"
        self.seen.append(tag)
        return json.dumps(
            {"entities": [{"name": f"人物{tag}", "type": "person"}], "relations": []},
            ensure_ascii=False,
        )


def test_backfill_offset_cursor_processes_different_entries(tmp_path) -> None:
    """分页契约：不推 offset 会重跑同一批（白烧 LLM），推了才真能走完全库。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("structured_memory_entries", _entries(3))
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    llm = _PromptAwareLLM()
    extractor = KnowledgeGraphExtractor(kg, ai_client=llm)
    reg = _backfill_ctx(tmp_path, extractor, kg, store)

    first = _run(reg.dispatch("knowledge.backfill", {"limit": 1}))
    second = _run(reg.dispatch("knowledge.backfill", {"limit": 1, "offset": first["next_offset"]}))
    third = _run(reg.dispatch("knowledge.backfill", {"limit": 1, "offset": second["next_offset"]}))

    assert (first["offset"], first["next_offset"], first["remaining"]) == (0, 1, 2), first
    assert (second["offset"], second["next_offset"], second["remaining"]) == (1, 2, 1), second
    assert (third["offset"], third["next_offset"], third["remaining"]) == (2, 3, 0), third
    assert llm.seen == ["1", "2", "3"], f"三批应各处理不同条目: {llm.seen!r}"
    names = sorted(e["name"] for e in kg.get_all_entities())
    assert names == ["人物1", "人物2", "人物3"], names


def test_backfill_batches_via_remaining(tmp_path) -> None:
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("structured_memory_entries", _entries(3))
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    llm = _LLM(_PAYLOAD)
    extractor = KnowledgeGraphExtractor(kg, ai_client=llm)
    reg = _backfill_ctx(tmp_path, extractor, kg, store)

    first = _run(reg.dispatch("knowledge.backfill", {"limit": 1}))
    second = _run(reg.dispatch("knowledge.backfill", {"limit": 1}))

    assert first["remaining"] == 2 and second["remaining"] == 2, (first, second)
    assert first["attempted"] == 1 and second["attempted"] == 1
    assert second["new_entities"] == 0, "同批重跑幂等：不得重复建点"
    assert second["new_relations"] == 0, "同批重跑幂等：边按同键归一不新增"
    assert len(kg.get_all_entities()) == 2, f"重跑同一批不得长出新节点: {kg.get_all_entities()!r}"


def test_backfill_refuses_without_ai_client(tmp_path) -> None:
    """无内核时不得拿正则冒充回填成果（KG-CH 契约在 RPC 层的延伸）。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    store.save_json("structured_memory_entries", _entries(2))
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    extractor = KnowledgeGraphExtractor(kg, ai_client=None)
    reg = _backfill_ctx(tmp_path, extractor, kg, store)

    res = _run(reg.dispatch("knowledge.backfill", {"limit": 5}))

    assert res.get("success") is False and res.get("error") == "no_ai_client", res
    assert kg.get_all_entities() == [], "拒绝时不得写图（尤其不得写正则噪声）"


def test_backfill_requires_graph_store(tmp_path) -> None:
    """没图谱存储才是真不可用，必须报错而不是空跑。"""
    ctx = SimpleNamespace(knowledge_graph_store=None, knowledge_graph_extractor=None, local_data_store=None)
    reg = MethodRegistry()
    register_inbox_knowledge(reg, ctx)

    res = _run(reg.dispatch("knowledge.backfill", {}))

    assert res.get("success") is False and "knowledge_graph_store" in str(res.get("error")), res


def test_backfill_reports_honestly_when_nothing_to_capture(tmp_path) -> None:
    """P4-KG-CAPTURE 后契约：采不到条目不再当错误（统一采集层里某一域仓储
    缺失只是那一域为空），但必须如实报 candidates=0 / attempted=0，不谎称经了一轮。"""
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    extractor = KnowledgeGraphExtractor(kg, ai_client=_LLM(_PAYLOAD))
    ctx = SimpleNamespace(
        knowledge_graph_store=kg,
        knowledge_graph_extractor=extractor,
        local_data_store=None,
        personal_note_repository=None,
        personal_inbox_repository=None,
        personal_task_repository=None,
        vocabulary_repository=None,
    )
    reg = MethodRegistry()
    register_inbox_knowledge(reg, ctx)

    res = _run(reg.dispatch("knowledge.backfill", {"domains": "all"}))

    assert res.get("success") is True, res
    assert res["candidates"] == 0 and res["attempted"] == 0, res


# ══════════════════════════════════════════════════════════════
# D. knowledge.graph 的 max_entities 上限
# ══════════════════════════════════════════════════════════════


def _store_for_cap(tmp_path) -> KnowledgeGraphStore:
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    kg.add_entity("e1", "连通甲", "concept")
    kg.add_entity("e2", "连通乙", "concept")
    kg.add_entity("e3", "孤立点", "concept")
    kg.add_relation("e1", "e2", "related_to")
    return kg


def test_graph_default_returns_all_and_is_backward_compatible(tmp_path) -> None:
    kg = _store_for_cap(tmp_path)
    reg, _ctx = _registry(tmp_path, kg)

    res = _run(reg.dispatch("knowledge.graph", {}))

    assert len(res["entities"]) == 3 and len(res["relations"]) == 1, res
    assert res["total_entities"] == 3 and res["truncated"] is False, res


def test_graph_max_entities_prefers_connected_nodes(tmp_path) -> None:
    kg = _store_for_cap(tmp_path)
    reg, _ctx = _registry(tmp_path, kg)

    res = _run(reg.dispatch("knowledge.graph", {"max_entities": 2}))

    ids = {e["id"] for e in res["entities"]}
    assert ids == {"e1", "e2"}, f"截断须优先保留连通节点: {ids!r}"
    assert res["truncated"] is True and res["total_entities"] == 3, res
    assert len(res["relations"]) == 1, "两端都在的边应保留"


def test_graph_max_entities_drops_dangling_edges(tmp_path) -> None:
    kg = _store_for_cap(tmp_path)
    reg, _ctx = _registry(tmp_path, kg)

    res = _run(reg.dispatch("knowledge.graph", {"max_entities": 1}))

    assert len(res["entities"]) == 1, res
    assert res["relations"] == [], f"对端被截掉的边不得留在返回里: {res['relations']!r}"
