"""P4-KG-CAPTURE（2026-09-26）· 多域采集层回归。

要补的缺口（上一轮分析实测）：图谱只接了 3 类来源（DeepSeek 导入、结构化记忆
回灌、MD 回灌），**笔记/收件箱/任务/词库四个域根本没有进图谱的通道** —— 于是
739 节点里 79% 是孤立点、65% 说不清自己从哪来。本文件锁定：五域都能被归一成
同一形状送抽取，且出处带域前缀（可溯源、可级联清理、可跳转）。
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
from knowledge.domain_capture import (  # noqa: E402
    CAPTURABLE_DOMAINS,
    capture_text,
    collect_capture_items,
    normalize_domains,
)
from knowledge.graph_extractor import KnowledgeGraphExtractor  # noqa: E402
from knowledge.graph_store import KnowledgeGraphStore  # noqa: E402


class _LLM:
    """假内核：把 prompt 里的「#域#」标记回提成实体名，用于证明处理了哪一条。"""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def complete_text(self, messages, temperature=0.1, max_tokens=1000, **kwargs):  # noqa: ANN001, D102
        import re

        text = str(messages[-1]["content"]) if messages else ""
        match = re.search(r"#(\w+)#", text)
        tag = match.group(1) if match else "?"
        self.seen.append(tag)
        return json.dumps({"entities": [{"name": f"域{tag}实体", "type": "concept"}], "relations": []}, ensure_ascii=False)


def _context(tmp_path, *, with_store=True, with_notes=True, with_inbox=True, with_tasks=True, with_vocab=True):
    store = (
        SimpleNamespace(
            base_dir=str(tmp_path),
            load_json=lambda key, default=None: (
                [
                    {"id": "m1", "title": "记忆一", "content": "王小明#memory#启动蓝鲸计划", "created_at": "2026-09-20"},
                ]
                if key == "structured_memory_entries"
                else (default if default is not None else [])
            ),
        )
        if with_store
        else None
    )

    notes = (
        SimpleNamespace(
            list_notes=lambda: [
                {"id": 42, "title": "周会记录#note#", "content": "确认蓝鲸计划排期", "created_at": "2026-09-21"},
                {"id": 43, "title": "", "content": "", "created_at": "2026-09-21"},
            ]
        )
        if with_notes
        else None
    )

    inbox = (
        SimpleNamespace(
            list_entries=lambda include_archived=False: [
                {"id": 7, "title": "灵感", "content": "试试图数据库#inbox#", "created_at": "2026-09-19"},
            ]
        )
        if with_inbox
        else None
    )

    tasks = (
        SimpleNamespace(
            list_tasks=lambda include_deleted=False: [
                {"id": 3, "title": "写周报#task#", "description": "汇总蓝鲸计划进展", "created_at": "2026-09-18"},
                {"id": 4, "title": "只有标题的任务#task2#", "description": "", "created_at": "2026-09-16"},
            ]
        )
        if with_tasks
        else None
    )

    vocab = (
        SimpleNamespace(
            list_words=lambda include_deleted=False: [
                {"id": 9, "word": "serendipity", "meaning": "意外发现珍宝的运气#vocab#", "created_at": "2026-09-17"},
            ]
        )
        if with_vocab
        else None
    )

    return SimpleNamespace(
        local_data_store=store,
        personal_note_repository=notes,
        personal_inbox_repository=inbox,
        personal_task_repository=tasks,
        vocabulary_repository=vocab,
    )


# ── 1. 入参归一 ───────────────────────────────────────────


def test_normalize_domains_defaults_to_memory_only() -> None:
    """缺省必须只跑 memory：不能因为加了参数就让旧调用悄悄扩到全域烧钱。"""
    assert normalize_domains(None) == ["memory"]
    assert normalize_domains("") == ["memory"]
    assert normalize_domains("bogus") == ["memory"]


def test_normalize_domains_accepts_all_list_and_csv_forms() -> None:
    assert normalize_domains("all") == list(CAPTURABLE_DOMAINS)
    assert normalize_domains(["note", "inbox", "note"]) == ["note", "inbox"]
    assert normalize_domains("note,inbox") == ["note", "inbox"]
    assert normalize_domains("note;task") == ["note", "task"]


# ── 2. 采集归一 ───────────────────────────────────────────


def test_collect_unifies_five_domains_into_one_shape(tmp_path) -> None:
    ctx = _context(tmp_path)

    items = collect_capture_items(ctx, "all")

    kinds = [i["kind"] for i in items]
    assert {"memory", "note", "inbox", "task", "vocab"} <= set(kinds), kinds
    for item in items:
        assert set(item) == {"kind", "id", "title", "content", "created_at"}, item
        assert item["content"], f"采集条目必须带上可抽取的正文: {item!r}"


def test_collect_orders_newest_first_and_skips_contentless_items(tmp_path) -> None:
    """无正文又无标题的不采（白烧一次 LLM）；**只有标题的仍采**——任务/词库
    常常没正文，用标题兜底是有意的（否则这两个域永远是空的）。"""
    ctx = _context(tmp_path)

    items = collect_capture_items(ctx, "all")

    dates = [i["created_at"] for i in items]
    assert dates == sorted(dates, reverse=True), dates
    assert all(i["id"] != "43" for i in items), "标题与正文都空的条目不该被采集"
    assert any(i["kind"] == "task" and i["content"] for i in items), "任务只有标题时应用标题兜底仍被采集"
    title_only = next(i for i in items if i["id"] == "4")
    assert title_only["content"] == "只有标题的任务#task2#", title_only


def test_missing_repository_only_loses_that_domain(tmp_path) -> None:
    ctx = _context(tmp_path, with_notes=False, with_store=False)

    items = collect_capture_items(ctx, "all")

    kinds = {i["kind"] for i in items}
    assert "note" not in kinds and "memory" not in kinds, kinds
    assert {"inbox", "task", "vocab"} <= kinds, kinds


def test_days_window_excludes_undated_items(tmp_path) -> None:
    ctx = _context(tmp_path)
    ctx.personal_note_repository = SimpleNamespace(list_notes=lambda: [{"id": 1, "title": "老笔记", "content": "x", "created_at": "2020-01-01"}])

    recent = collect_capture_items(ctx, "note", days=30)
    assert recent == [], recent


def test_capture_text_does_not_duplicate_title(tmp_path) -> None:
    assert capture_text({"title": "蓝鲸", "content": "蓝鲸计划启动"}) == "蓝鲸计划启动"
    assert capture_text({"title": "周会", "content": "排期确认"}) == "周会 | 排期确认"
    assert capture_text({"title": "只有标题", "content": ""}) == "只有标题"


# ── 3. RPC 端到端：笔记真的进了图谱且出处可溯 ──────────────


def _registry(ctx, kg, extractor):
    ctx.knowledge_graph_store = kg
    ctx.knowledge_graph_extractor = extractor
    reg = MethodRegistry()
    register_inbox_knowledge(reg, ctx)
    return reg


def test_backfill_note_domain_writes_origin_with_note_namespace(tmp_path) -> None:
    ctx = _context(tmp_path)
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    llm = _LLM()
    reg = _registry(ctx, kg, KnowledgeGraphExtractor(kg, ai_client=llm))

    res = asyncio.run(reg.dispatch("knowledge.backfill", {"domains": "note", "limit": 5}))

    assert res.get("success") is True, res
    assert res["domains"] == ["note"] and res["attempted"] == 1, res
    assert llm.seen == ["note"], f"只应处理笔记域条目: {llm.seen!r}"
    entity = next(e for e in kg.get_all_entities() if e["name"] == "域note实体")
    assert entity["properties"]["origins"] == [{"kind": "note", "id": "42"}], entity
    # 出处带域 → 级联清理才认得它（笔记删除不再留幽灵实体）
    assert kg.remove_by_source("note", 42) == 1


def test_backfill_all_domains_walks_every_domain(tmp_path) -> None:
    ctx = _context(tmp_path)
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    llm = _LLM()
    reg = _registry(ctx, kg, KnowledgeGraphExtractor(kg, ai_client=llm))

    res = asyncio.run(reg.dispatch("knowledge.backfill", {"domains": "all", "limit": 20}))

    # 不钉死条数（夹具会随域新增条目变动），只钉住“五域都被走到、一批跑完不剩”
    assert res["attempted"] == res["candidates"] > 0, res
    assert set(res["by_domain"]) == {"memory", "note", "inbox", "task", "vocab"}, res["by_domain"]
    assert {"memory", "note", "inbox", "task", "vocab"} <= {s.split("2")[0] for s in llm.seen}, llm.seen
    assert res["remaining"] == 0 and res["next_offset"] == res["attempted"], res


def test_backfill_default_domains_is_memory_only(tmp_path) -> None:
    ctx = _context(tmp_path)
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    llm = _LLM()
    reg = _registry(ctx, kg, KnowledgeGraphExtractor(kg, ai_client=llm))

    res = asyncio.run(reg.dispatch("knowledge.backfill", {"limit": 10}))

    assert res["domains"] == ["memory"] and res["attempted"] == 1, res
    assert llm.seen == ["memory"], llm.seen


def test_backfill_survives_broken_repository(tmp_path) -> None:
    """某域仓储抛异常只该让它自己为空，不能炸掉整轮回填（也不谎报成功）。"""
    ctx = _context(tmp_path)
    ctx.personal_inbox_repository = SimpleNamespace(list_entries=lambda include_archived=False: (_ for _ in ()).throw(RuntimeError("表坏了")))
    kg = KnowledgeGraphStore(path=tmp_path / "kg.json")
    reg = _registry(ctx, kg, KnowledgeGraphExtractor(kg, ai_client=_LLM()))

    res = asyncio.run(reg.dispatch("knowledge.backfill", {"domains": "all", "limit": 10}))

    assert res.get("success") is True, res
    assert "inbox" not in res["by_domain"], res
    assert {"memory", "note"} <= set(res["by_domain"]), res
