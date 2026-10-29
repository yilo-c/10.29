"""knowledge.detail_backfill 存量详情回填测试（P4-KG-DETAIL 批次 C，2026-10-15）。

锁死的契约：
1. dry_run 默认 True —— 只预览不落盘（防误改生产图谱）；
2. LLM 归纳 → detail_via="llm"；无 AI → 原文首句截断 detail_via="extract"；
3. LLM 回答「无」→ 降级 extract（不把"无"当详情写进去）；
4. 出处反查不到原文 → 跳过（宁缺毋滥，绝不用实体名编造）；
5. LLM 调用异常 → 该实体记 failed，不阻断整批；
6. 只写 detail/detail_via/detail_sources —— name/type 原样回传不改。
"""

from __future__ import annotations

from typing import Any, cast

from knowledge.detail_backfill import backfill_entity_details
from knowledge.graph_store import KnowledgeGraphStore


def _run(store: Any, **kwargs: Any) -> dict[str, Any]:
    """统一入口：桩 store 经 cast 过 mypy 签名（运行时行为不变）。"""
    return backfill_entity_details(cast(KnowledgeGraphStore, store), **kwargs)


class _FakeAI:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls = 0

    def complete_text(self, messages, **kwargs):  # noqa: ANN001
        self.calls += 1
        return self._reply


class _BoomAI:
    def complete_text(self, messages, **kwargs):  # noqa: ANN001
        raise RuntimeError("quota")


class _StoreStub:
    def __init__(self, entities: list[dict]) -> None:
        self._entities = entities
        self.saved = False
        self.add_calls: list[dict] = []

    def get_all_entities(self):  # noqa: ANN001
        return self._entities

    def add_entity(self, entity_id, name, entity_type="unknown", aliases=None, properties=None):  # noqa: ANN001
        self.add_calls.append({"id": entity_id, "name": name, "type": entity_type, "properties": properties})
        return entity_id

    def save(self):  # noqa: ANN001
        self.saved = True


def _entity(eid: str, name: str, *, detail: str = "", sources: list[str] | None = None) -> dict:
    return {
        "id": eid,
        "name": name,
        "type": "project",
        "properties": {"detail": detail, "sources": sources or []},
    }


_NOTE_RESOLVER = lambda ident: {"title": f"笔记{ident}", "content": f"笔记{ident}的正文内容"}  # noqa: E731


def test_dry_run_default_does_not_write() -> None:
    """dry_run 默认 True：只预览，不落盘（防误改生产图谱）。"""
    store = _StoreStub([_entity("e1", "蓝鲸计划", sources=["note:1"])])
    report = _run(store, resolvers={"note": _NOTE_RESOLVER}, ai_client=_FakeAI("数据平台重构项目"))

    assert report["dry_run"] is True
    assert report["filled"] == 1
    assert store.add_calls == []
    assert store.saved is False
    assert report["items"][0]["detail"] == "数据平台重构项目"


def test_llm_detail_written_with_via_marker() -> None:
    """LLM 归纳 → 落库 + detail_via="llm" + detail_sources 记录出处。"""
    store = _StoreStub([_entity("e1", "蓝鲸计划", sources=["note:1"])])
    report = _run(store, resolvers={"note": _NOTE_RESOLVER}, ai_client=_FakeAI("数据平台重构项目"), dry_run=False)

    assert report["filled"] == 1
    assert store.saved is True
    call = store.add_calls[0]
    assert call["properties"]["detail"] == "数据平台重构项目"
    assert call["properties"]["detail_via"] == "llm"
    assert call["properties"]["detail_sources"] == ["note:1"]


def test_no_ai_falls_back_to_extract_snippet() -> None:
    """无 ai_client → 原文首句截断（detail_via="extract"），不空白。"""
    store = _StoreStub([_entity("e1", "蓝鲸计划", sources=["note:1"])])
    report = _run(store, resolvers={"note": _NOTE_RESOLVER}, ai_client=None, dry_run=False)

    item = report["items"][0]
    assert item["via"] == "extract"
    assert "笔记1" in item["detail"]
    assert store.add_calls[0]["properties"]["detail_via"] == "extract"


def test_llm_replies_none_falls_back_to_extract() -> None:
    """LLM 回答「无」→ 不把"无"当详情，降级 extract。"""
    store = _StoreStub([_entity("e1", "蓝鲸计划", sources=["note:1"])])
    report = _run(store, resolvers={"note": _NOTE_RESOLVER}, ai_client=_FakeAI("无"), dry_run=False)

    assert report["items"][0]["via"] == "extract"
    assert store.add_calls[0]["properties"]["detail"] != "无"


def test_no_resolvable_source_skipped() -> None:
    """出处反查不到原文 → skipped（绝不用实体名编造详情）。"""
    store = _StoreStub([_entity("e1", "开源项目", sources=[]), _entity("e2", "大城市", sources=["unknown:x"])])
    report = _run(store, resolvers={"note": _NOTE_RESOLVER}, ai_client=_FakeAI("某详情"), dry_run=False)

    assert report["skipped_no_source"] == 2
    assert report["filled"] == 0
    assert store.add_calls == []


def test_llm_failure_isolated_per_entity() -> None:
    """LLM 调用异常 → 该实体 failed，不阻断整批（其余实体照常）。"""
    store = _StoreStub([_entity("e1", "A项目", sources=["note:1"]), _entity("e2", "B项目", sources=["note:2"])])
    report = _run(store, resolvers={"note": _NOTE_RESOLVER}, ai_client=_BoomAI(), dry_run=False)

    assert report["failed"] == 2
    assert report["filled"] == 0
    assert store.saved is False  # 无可写项则不落盘


def test_only_missing_entities_processed() -> None:
    """已有 detail 的实体不重复处理（只补缺口）。"""
    store = _StoreStub([_entity("e1", "已完成", detail="已有详情", sources=["note:1"])])
    report = _run(store, resolvers={"note": _NOTE_RESOLVER}, ai_client=_FakeAI("新详情"), dry_run=False)

    assert report["missing"] == 0
    assert report["filled"] == 0
    assert store.add_calls == []


def test_bare_id_resolved_via_candidate_kinds() -> None:
    """裸 id 出处（历史 445 条无命名空间）按候选族逐个反查——node_profile 同口径。

    10-15 实跑预览发现：裸 id 只按 parse_origin 的 kind=unknown 反查，而
    resolvers 没有 unknown 域 → 前 20 个实体全 skipped。本测试锁死候选族
    反查契约（memory/md/info/note 逐个试，命中即定名）。
    """
    store = _StoreStub([_entity("e1", "某项目", sources=["abc123"])])

    def _memory_body(ident: str) -> dict[str, Any] | None:
        if ident == "abc123":
            return {"title": "记忆条目", "content": "某项目是一个数据平台重构项目"}
        return None

    report = _run(store, resolvers={"memory": _memory_body}, ai_client=None, dry_run=False)

    assert report["filled"] == 1
    call = store.add_calls[0]
    assert call["properties"]["detail_sources"] == ["memory:abc123"]
    assert "某项目" in call["properties"]["detail"]
