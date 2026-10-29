"""知识图谱「节点详情」归一层 —— 多通道产出的统一转化（P4-KG-DETAIL，2026-09-26）。

要治的病（实测确证，见 docs/ 与 AGENTS.md 知识图谱节）：图谱有 6 条以上写入通道，
每条往 `properties` 里写的键各不相同，而消费侧（桌面详情面板）只认其中一种形态，
导致节点点开**没有完整信息可显示**：

    ent_*   656 个 → properties = {sources: [...], topic?}
    card_*   75 个 → properties = {kind, title, topic, sources?}
    moc_*     8 个 → properties = {kind, topic}
    external_content_* → properties = {source_id: 裸 id}   ← 唯一带 source_id 的一族

真实图谱 739 实体里：`source_id` 0 个（「打开来源笔记」按钮恒不出现）、
`sources` 658 个（**是数组，被前端 propertyEntries 的 typeof!=='object' 过滤掉，
一条都不显示**）、正文/摘要 0 个、别名 14 个（前端转换时丢弃）、有邻居的仅 20%。

本模块把「这个节点是什么、从哪来、连着谁、有没有正文可看」收敛成**一个**结构，
由 `knowledge.entity.detail` 一次返回；出处命名空间（`note:` / `deepseek:` / 裸 id）
在这里统一解析，前端不再猜键名。与 `backend/handlers/_timeline.py` 的
`_build_entry(source_type=…, title=…, summary=…)` 是同一范式——那条链路早就这么
做所以时间轴能渲染统一卡片，图谱域此前缺这一层。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from .graph_store import KnowledgeGraphStore

logger = logging.getLogger("siyi.knowledge.node_profile")

# ── 出处命名空间词表（单点定义，写侧与读侧共用）───────────────────
#
# jump_module 非空 = 前端可以真的跳过去打开；为空 = 只显示「来自某外部对话」，
# 不给假链接（本地根本没有那份数据）。

ORIGIN_KINDS: dict[str, dict[str, Any]] = {
    "note": {"label": "笔记", "jump_module": "notes"},
    "task": {"label": "待办", "jump_module": "todos"},
    "inbox": {"label": "收件箱", "jump_module": "inbox"},
    "info": {"label": "资讯", "jump_module": "info"},
    "memory": {"label": "记忆条目", "jump_module": ""},
    "md": {"label": "知识库文件", "jump_module": "knowledge"},
    "reading_article": {"label": "阅读文章", "jump_module": "reading"},
    "external_content": {"label": "外部内容", "jump_module": "reading"},
    "external_source": {"label": "外部来源", "jump_module": ""},
    "vocab": {"label": "词库", "jump_module": "vocab"},
    # 外部 AI 平台的会话镜像：不可跳转，只作溯源展示
    "deepseek": {"label": "DeepSeek 对话", "jump_module": ""},
    "kimi": {"label": "Kimi 对话", "jump_module": ""},
    "unknown": {"label": "来源条目", "jump_module": ""},
}

# 裸 id（无命名空间）最可能来自哪一族——按历史写入端排序，供反查顺序使用
BARE_ID_CANDIDATE_KINDS: tuple[str, ...] = ("memory", "md", "info", "note")

# 实体类型受控词表之外的历史值（如脚本产出的 knowledge.card）在此归一
TYPE_ALIASES: dict[str, str] = {
    "knowledge.card": "concept",
    "knowledge.moc": "concept",
    "external_content": "concept",
    "external_source": "organization",
}


def normalize_entity_type(raw: str | None) -> str:
    """把通道自造的类型词归一到展示用词表（不改存储，只影响展示口径）。"""
    key = str(raw or "").strip()
    if not key:
        return "unknown"
    return TYPE_ALIASES.get(key, key)


def origin_display_kind(kind: str) -> dict[str, Any]:
    return ORIGIN_KINDS.get(str(kind or "").strip(), ORIGIN_KINDS["unknown"])


def parse_origin(raw: Any) -> dict[str, Any]:
    """把一条出处字符串解析成 `{kind, source_id, resolvable, label, jump_module}`。

    兼容三代写法：`"note:42"`（规范）、`"kimi_17123..."`（Kimi 通道用下划线）、
    `"42"` / uuid（**裸 id，来自记忆条目或 MD 回灌，命名空间已丢失**）。
    裸 id 归 `unknown` 并标记待反查——本模块不猜它是笔记还是记忆。
    """
    text = str(raw or "").strip()
    if not text:
        return {"kind": "unknown", "source_id": "", "resolved": False, "raw": ""}
    if ":" in text:
        head, _, tail = text.partition(":")
        kind = head.strip()
        ident = tail.strip()
        if kind in ORIGIN_KINDS and ident:
            display = origin_display_kind(kind)
            return {
                "kind": kind,
                "source_id": ident,
                "resolved": True,
                "raw": text,
                "label": display["label"],
                "jump_module": display["jump_module"],
            }
    if text.startswith("kimi_"):
        display = origin_display_kind("kimi")
        return {
            "kind": "kimi",
            "source_id": text[len("kimi_") :],
            "resolved": True,
            "raw": text,
            "label": display["label"],
            "jump_module": display["jump_module"],
        }
    display = origin_display_kind("unknown")
    return {
        "kind": "unknown",
        "source_id": text,
        "resolved": False,
        "raw": text,
        "label": display["label"],
        "jump_module": display["jump_module"],
    }


def collect_raw_origins(entity: Mapping[str, Any]) -> list[str]:
    """按「结构化 origins → sources → source_id」优先级收集原始出处串（去重保序）。"""
    props = entity.get("properties") if isinstance(entity.get("properties"), Mapping) else {}
    ordered: list[str] = []
    structured = props.get("origins") if isinstance(props, Mapping) else None
    if isinstance(structured, list):
        for item in structured:
            if isinstance(item, Mapping):
                kind = str(item.get("kind") or "unknown")
                ident = str(item.get("id") or "")
                if ident:
                    ordered.append(f"{kind}:{ident}" if kind != "unknown" else ident)
            elif item:
                ordered.append(str(item))
    for key in ("sources", "source_id"):
        value = (props or {}).get(key)
        if isinstance(value, list):
            ordered.extend(str(v) for v in value if v)
        elif value:
            ordered.append(str(value))
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in ordered:
        if raw in seen:
            continue
        seen.add(raw)
        deduped.append(raw)
    return deduped


def _entity_brief(store: KnowledgeGraphStore, entity_id: str) -> dict[str, Any]:
    entity = store.get_entity(entity_id) or {}
    props = entity.get("properties") if isinstance(entity.get("properties"), Mapping) else {}
    return {
        "id": entity_id,
        "name": entity.get("name") or "",
        "type": entity.get("type") or "unknown",
        "type_display": normalize_entity_type(entity.get("type")),
        "aliases": list(entity.get("aliases") or []),
        "topic": str((props or {}).get("topic") or "").strip(),
        "kind": str((props or {}).get("kind") or "").strip(),
        "title": str((props or {}).get("title") or "").strip(),
        # P4-KG-DETAIL（2026-10-15）：实体详情（「这个实体是什么」）。LLM 提取时
        # 一次性归纳落 properties.detail；存量实体经 detail_backfill 回填。
        # 空 = 该实体暂无详情（不编造，前端显示「暂无详情」而非假内容）。
        "detail": str((props or {}).get("detail") or "").strip(),
        "created_at": entity.get("created_at") or "",
        "updated_at": entity.get("updated_at") or "",
        "source_id": str((props or {}).get("source_id") or "").strip(),
    }


def _neighbors(store: KnowledgeGraphStore, entity_id: str) -> list[dict[str, Any]]:
    """邻居按「谓词 + 方向」展开，带对端名称/类型——前端无需再自己拼。"""
    rows: list[dict[str, Any]] = []
    for rel in store.get_relations(entity_id):
        rel_type = str(rel.get("relation_type") or "related_to")
        src = str(rel.get("source_id") or "")
        dst = str(rel.get("target_id") or "")
        other_id, direction = (dst, "out") if src == entity_id else (src, "in")
        other = store.get_entity(other_id) or {}
        rows.append(
            {
                "relation_type": rel_type,
                "direction": direction,
                "other_id": other_id,
                "other_name": other.get("name") or other_id,
                "other_type": normalize_entity_type(other.get("type")),
                "confidence": rel.get("confidence", 1.0),
                "description": str(((rel.get("properties") or {}) if isinstance(rel.get("properties"), Mapping) else {}).get("description") or ""),
            }
        )
    rows.sort(key=lambda r: (r["relation_type"], r["other_name"]))
    return rows


def build_entity_detail(
    store: KnowledgeGraphStore,
    entity_id: str,
    *,
    resolvers: Mapping[str, Callable[[str], dict[str, Any] | None]] | None = None,
    card_loader: Callable[[str], dict[str, Any] | None] | None = None,
    max_origins: int = 20,
    max_neighbors: int = 30,
) -> dict[str, Any]:
    """把一个图谱实体转成「可完整显示」的详情结构。

    Args:
        store: 图存储。
        entity_id: 实体 id（前端 `bk-` 前缀需调用方先剥）。
        resolvers: kind → 反查函数，用于把出处补成「有标题、可跳转」。反查失败
            不影响返回（只是少一条标题），绝不抛。
        card_loader: 按 id 取知识卡片/MOC 正文（实体本身不存正文，只能回查）。

    Returns:
        ``{found, entity, origins, unresolved_origin_count, relations,
        relations_by_predicate, card, has_body_text}``；实体不存在时
        ``{"found": False, "entity_id": ...}``（不编造空详情）。
    """
    entity_id = str(entity_id or "").strip()
    if not entity_id:
        return {"found": False, "entity_id": "", "error": "entity_id required"}
    if store.get_entity(entity_id) is None:
        return {"found": False, "entity_id": entity_id, "error": "not found"}

    brief = _entity_brief(store, entity_id)
    resolvers = resolvers or {}

    origins: list[dict[str, Any]] = []
    unresolved = 0
    for raw in collect_raw_origins(store.get_entity(entity_id) or {})[: max(1, int(max_origins))]:
        parsed = parse_origin(raw)
        item: dict[str, Any] = {
            "raw": parsed["raw"],
            "kind": parsed["kind"],
            "source_id": parsed["source_id"],
            "label": parsed["label"],
            "target_module": parsed["jump_module"],
            # 默认：命名空间已知且有目标模块 = 可跳；裸 id 一律先判不可跳
            "jumpable": bool(parsed["resolved"] and parsed["jump_module"]),
            "title": "",
            "resolved": bool(parsed["resolved"]),
        }
        if not parsed["resolved"]:
            unresolved += 1
            # 裸 id：按候选族逐个反查（不猜），命中即定名并允许跳转
            for guess in BARE_ID_CANDIDATE_KINDS:
                resolver = resolvers.get(guess)
                if resolver is None:
                    continue
                try:
                    hit = resolver(str(parsed["source_id"]))
                except Exception as exc:  # noqa: BLE001 — 反查失败只降级为不可跳
                    logger.debug("origin resolve failed %s:%s: %s", guess, parsed["source_id"], exc)
                    continue
                if not hit:
                    continue
                display = origin_display_kind(guess)
                item.update(
                    {
                        "kind": guess,
                        "label": display["label"],
                        "target_module": display["jump_module"],
                        "title": str(hit.get("title") or "")[:120],
                        "resolved": True,
                        "inferred": True,
                        "jumpable": bool(display["jump_module"]),
                    }
                )
                unresolved -= 1
                break
        else:
            resolver = resolvers.get(item["kind"])
            if resolver is not None:
                try:
                    hit = resolver(str(item["source_id"]))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("origin resolve failed %s:%s: %s", item["kind"], item["source_id"], exc)
                    hit = None
                if hit:
                    item["title"] = str(hit.get("title") or "")[:120]
        origins.append(item)

    relations_all = _neighbors(store, entity_id)
    predicate_counts: dict[str, int] = {}
    for row in relations_all:
        predicate_counts[row["relation_type"]] = predicate_counts.get(row["relation_type"], 0) + 1

    card: dict[str, Any] | None = None
    if card_loader is not None:
        # 卡片/MOC 节点的正文不在实体上，在 structured_memory_entries —— 回查才能显示
        for probe in (brief["id"], brief["title"] or "", brief["name"]):
            key = str(probe or "").strip()
            if not key:
                continue
            try:
                card = card_loader(key)
            except Exception as exc:  # noqa: BLE001
                logger.debug("card load failed for %s: %s", key, exc)
                card = None
            if card:
                break

    has_body_text = bool(card and (card.get("content") or card.get("detail") or card.get("analysis")))
    return {
        "found": True,
        "entity": brief,
        "degree": len(relations_all),
        "origins": origins,
        "unresolved_origin_count": unresolved,
        "relations": relations_all[: max(1, int(max_neighbors))],
        "relations_by_predicate": predicate_counts,
        "card": card,
        "has_body_text": has_body_text,
    }
