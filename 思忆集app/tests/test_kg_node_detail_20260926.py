"""P4-KG-DETAIL（2026-09-26）· 节点详情归一层回归。

治的病（真实图谱实测）：739 实体里 `properties.source_id` **0 个**、
`properties.sources` 658 个但是**数组**（前端 propertyEntries 的 typeof!=='object'
直接丢弃）、正文 0 个、有邻居的仅 20% —— 所以"点开节点没有完整信息可显示"。

本模块锁定：读侧一次聚合就能拿到 出处（含标题/可跳标记）+ 邻居分组 + 卡片正文，
且不可跳转的外部来源绝不给假链接。
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._inbox_knowledge import register_inbox_knowledge  # noqa: E402
from backend.method_registry import MethodRegistry  # noqa: E402
from knowledge.graph_store import KnowledgeGraphStore  # noqa: E402
from knowledge.node_profile import build_entity_detail, collect_raw_origins, normalize_entity_type, parse_origin  # noqa: E402


def _store(tmp_path) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(path=tmp_path / "kg.json")
    store.add_entity("ent_a", "蓝鲸计划", "project")
    store.add_entity("ent_b", "王小明", "person")
    store.add_entity("ent_iso", "孤岛点", "concept")
    store.add_relation("ent_b", "ent_a", "part_of")
    store.add_relation("ent_a", "ent_b", "related_to")
    store.add_entity_source("ent_a", "note:42")
    store.add_entity_source("ent_a", "deepseek:conv-9")
    store.add_entity_source("ent_a", "bare-entry-7")
    store.add_entity_source("ent_b", "kimi_1712345678")
    return store


# ── 出处解析 ──────────────────────────────────────────────


def test_parse_origin_handles_three_generations_of_writing() -> None:
    """规范 `note:42`、Kimi 的下划线式、以及丢了命名空间的裸 id 都要认。"""
    named = parse_origin("note:42")
    assert (named["kind"], named["source_id"], named["resolved"]) == ("note", "42", True)

    kimi = parse_origin("kimi_1712345678")
    assert kimi["kind"] == "kimi" and kimi["source_id"] == "1712345678", kimi

    bare = parse_origin("bare-entry-7")
    assert bare["kind"] == "unknown" and bare["resolved"] is False, bare


def test_collect_raw_origins_dedupes_across_fields(tmp_path) -> None:
    store = _store(tmp_path)
    raws = collect_raw_origins(store.get_entity("ent_a") or {})
    assert len(raws) == len(set(raws)), f"出处不得重复: {raws!r}"
    assert set(raws) == {"note:42", "deepseek:conv-9", "bare-entry-7"}, raws


def test_normalize_entity_type_folds_channel_invented_types() -> None:
    """各通道自造的类型词（脚本/外部内容）归一到可展示口径。"""
    assert normalize_entity_type("external_content") == "concept"
    assert normalize_entity_type("knowledge.card") == "concept"
    assert normalize_entity_type("project") == "project"
    assert normalize_entity_type("") == "unknown"


# ── 详情聚合 ──────────────────────────────────────────────


def test_detail_surfaces_origins_the_frontend_used_to_drop(tmp_path) -> None:
    """核心回归：sources 是数组，过去被 propertyEntries 丢弃 → 现在必须可见。"""
    store = _store(tmp_path)

    detail = build_entity_detail(store, "ent_a")

    assert detail["found"] is True, detail
    assert len(detail["origins"]) == 3, f"3 条出处都要显示: {detail['origins']!r}"
    kinds = {o["kind"] for o in detail["origins"]}
    assert "note" in kinds and "deepseek" in kinds, kinds


def test_external_origins_are_never_offered_as_jumps(tmp_path) -> None:
    """DeepSeek/Kimi 是外部平台镜像，本地没有这份数据 → 不给假链接。"""
    store = _store(tmp_path)

    detail = build_entity_detail(store, "ent_a")
    deep = next(o for o in detail["origins"] if o["kind"] == "deepseek")

    assert deep["jumpable"] is False, f"外部来源被标成可跳是假链接: {deep!r}"
    assert deep["target_module"] == "", deep


def test_bare_id_resolved_via_repo_becomes_jammable_note(tmp_path) -> None:
    """裸 id 不猜；只有反查命中才定名并允许跳转。"""
    store = _store(tmp_path)
    resolvers = {"note": lambda ident: {"title": "蓝鲸项目笔记"} if ident == "bare-entry-7" else None}

    detail = build_entity_detail(store, "ent_a", resolvers=resolvers)
    hit = next(o for o in detail["origins"] if o["source_id"] == "bare-entry-7")

    assert hit["kind"] == "note" and hit["jumpable"] is True, hit
    assert hit["title"] == "蓝鲸项目笔记" and hit.get("inferred") is True, hit
    assert detail["unresolved_origin_count"] == 0, detail


def test_resolver_failure_degrades_instead_of_raising(tmp_path) -> None:
    store = _store(tmp_path)

    def _boom(_ident: str) -> dict:
        raise RuntimeError("仓储炸了")

    detail = build_entity_detail(store, "ent_a", resolvers={"note": _boom, "memory": _boom})

    assert detail["found"] is True, detail
    note_origin = next(o for o in detail["origins"] if o["kind"] == "note")
    assert note_origin["title"] == "" and note_origin["jumpable"] is True, f"反查失败只少标题，跳转能力仍在: {note_origin!r}"


def test_detail_groups_neighbors_with_direction_and_names(tmp_path) -> None:
    store = _store(tmp_path)

    detail = build_entity_detail(store, "ent_a")

    assert detail["degree"] == 2, detail
    by_pred = detail["relations_by_predicate"]
    assert by_pred.get("part_of") == 1 and by_pred.get("related_to") == 1, by_pred
    inbound = next(r for r in detail["relations"] if r["relation_type"] == "part_of")
    assert inbound["direction"] == "in" and inbound["other_name"] == "王小明", inbound


def test_isolated_entity_reports_zero_degree_not_missing(tmp_path) -> None:
    """79% 节点无邻居：要如实返回 0，而不是让前端以为详情没加载。"""
    store = _store(tmp_path)

    detail = build_entity_detail(store, "ent_iso")

    assert detail["found"] is True and detail["degree"] == 0, detail
    assert detail["relations"] == [] and detail["has_body_text"] is False, detail


def test_card_body_is_loaded_from_memory_store(tmp_path) -> None:
    """卡片节点本身不存正文，必须回查 structured_memory_entries 才有的可看。"""
    store = _store(tmp_path)
    store.add_entity("card_1", "量子计算速记", "concept", properties={"kind": "knowledge_card", "title": "量子计算速记"})

    def _loader(key: str) -> dict | None:
        if key in ("card_1", "量子计算速记"):
            return {
                "entity_type": "knowledge.card",
                "title": "量子计算速记",
                "content": "叠加态与纠缠",
                "detail": "",
                "expansion": "",
                "analysis": "",
            }
        return None

    detail = build_entity_detail(store, "card_1", card_loader=_loader)

    assert detail["has_body_text"] is True, detail
    assert detail["card"]["content"] == "叠加态与纠缠", detail


def test_missing_entity_is_not_fabricated(tmp_path) -> None:
    store = _store(tmp_path)

    detail = build_entity_detail(store, "ghost")

    assert detail["found"] is False and detail["error"] == "not found", detail


# ── RPC 层 ────────────────────────────────────────────────


def _registry(tmp_path, kg, store=None):
    ctx = SimpleNamespace(
        knowledge_graph_store=kg,
        local_data_store=store,
        personal_note_repository=SimpleNamespace(get_note=lambda i: {"title": "笔记标题"} if str(i) == "42" else None),
        personal_inbox_repository=None,
    )
    reg = MethodRegistry()
    register_inbox_knowledge(reg, ctx)
    return reg


def test_rpc_detail_returns_origins_and_note_title(tmp_path) -> None:
    kg = _store(tmp_path)
    reg = _registry(tmp_path, kg, store=SimpleNamespace(load_json=lambda k, d=None: [], base_dir=str(tmp_path)))

    res = asyncio.run(reg.dispatch("knowledge.entity.detail", {"entity_id": "ent_a"}))

    assert res["found"] is True, res
    note_origin = next(o for o in res["origins"] if o["kind"] == "note")
    assert note_origin["title"] == "笔记标题" and note_origin["target_module"] == "notes", note_origin


def test_rpc_detail_requires_entity_id_and_reports_missing_store(tmp_path) -> None:
    kg = _store(tmp_path)
    reg = _registry(tmp_path, kg, store=SimpleNamespace(load_json=lambda k, d=None: [], base_dir=str(tmp_path)))

    assert asyncio.run(reg.dispatch("knowledge.entity.detail", {}))["found"] is False
    no_store = MethodRegistry()
    register_inbox_knowledge(no_store, SimpleNamespace(knowledge_graph_store=None, local_data_store=None))
    res = asyncio.run(no_store.dispatch("knowledge.entity.detail", {"entity_id": "ent_a"}))
    assert res["found"] is False and "unavailable" in str(res.get("error")), res
