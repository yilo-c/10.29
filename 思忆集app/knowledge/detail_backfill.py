"""知识图谱存量实体详情回填（P4-KG-DETAIL 批次 C，2026-10-15）。

治的病：写侧 prompt 历史上从未要求 LLM 产出 detail（2026-10-15 批次 B 才补上），
导致生产图谱 739 个实体 **0 个有详情**——详情面板打开只有名字/类型/出处，
没有「这个实体是什么」。新导入的实体自此自带 detail；本模块负责把存量补上。

设计纪律（与 node_profile / graph_extractor 同范式）：
1. **反查原文，不编造**：detail 只能从实体出处（properties.sources/origins）
   反查到的原文归纳；反查不到原文的实体**跳过**（留待接入更多域），绝不用
   实体名让 AI 百科式扩写——那正是 extraction prompt 明令禁止的行为。
2. **resolvers 注入**：kind → 反查函数由调用方提供（handler 层接各域仓储），
   本模块不 import 任何仓储——与 ``node_profile.build_entity_detail`` 一致。
3. **降级可追溯**：无 ai_client 时不产 LLM 归纳，改为原文首句截断并写
   ``detail_via="extract"``；LLM 归纳写 ``detail_via="llm"``。消费侧能分辨
   详情是"AI 读过的"还是"原文摘的"。
4. **dry_run 先行**：默认只预览（哪些实体能补、原文够不够），不落盘；
   实跑必须显式 ``dry_run=False``——它会改生产图谱文件。
5. **只增不毁**：只写 ``properties.detail/detail_via/detail_sources``，
   不触碰 name/type/aliases/relations/sources。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from .graph_store import KnowledgeGraphStore
from .node_profile import BARE_ID_CANDIDATE_KINDS, collect_raw_origins, parse_origin

logger = logging.getLogger("siyi.knowledge.detail_backfill")

# 单实体归纳 prompt：与 _EXTRACT_ENTITIES_PROMPT 的 detail 要求同口径
_DETAIL_PROMPT = (
    "根据下面的原文，用 1-2 句简体中文说明「{name}」是什么、做了什么、或与什么有关。\n"
    "只写原文确实支持的内容；禁止百科式扩写；禁止把话翻来覆去说。\n"
    "原文里没有关于「{name}」的可归纳信息时，只回答两个字：无。\n\n"
    "原文：\n{text}\n"
)

# 反查原文的域优先级（同一实体多出处时按此序取原文）
_ORIGIN_KIND_ORDER = ("note", "inbox", "memory", "md", "info", "reading_article")

# 单个实体拼接原文上限（与 _extract_with_llm 的 text[:2000] 同口径）
_MAX_SOURCE_TEXT = 2000
# extract 降级截断长度
_EXTRACT_SNIPPET = 100


def _resolve_one(
    parsed: dict[str, Any],
    resolvers: Mapping[str, Callable[[str], dict[str, Any] | None]],
) -> tuple[dict[str, Any] | None, str]:
    """反查单条出处，返回 (命中内容, 实际命中的 kind)。

    带命名空间的按 kind 精确反查；裸 id（kind=unknown，历史 445 条）按
    BARE_ID_CANDIDATE_KINDS 逐个试——与 node_profile 同口径（不猜，命中即定）。
    """
    kinds: list[str] = [parsed["kind"]] if parsed["resolved"] else []
    if not parsed["resolved"]:
        kinds.extend(BARE_ID_CANDIDATE_KINDS)
    for kind in kinds:
        resolver = resolvers.get(kind)
        if resolver is None:
            continue
        try:
            hit = resolver(str(parsed["source_id"]))
        except Exception as exc:  # noqa: BLE001 — 单条反查失败只跳过该条
            logger.debug("backfill: origin resolve failed %s:%s: %s", kind, parsed["source_id"], exc)
            continue
        if hit and str(hit.get("content") or hit.get("detail") or "").strip():
            return hit, kind
    return None, parsed["kind"]


def _gather_source_text(
    entity: Mapping[str, Any],
    resolvers: Mapping[str, Callable[[str], dict[str, Any] | None]],
    *,
    max_origins: int = 5,
) -> tuple[str, list[str]]:
    """按出处反查原文，返回 (拼接文本, 命中出处列表)。反查失败静默跳过。"""
    parts: list[str] = []
    hits: list[str] = []
    raws = collect_raw_origins(entity)
    # 按域优先级排序，note/inbox/memory 的原文质量高于资讯摘要
    parsed = [parse_origin(r) for r in raws]
    parsed.sort(key=lambda p: _ORIGIN_KIND_ORDER.index(p["kind"]) if p["kind"] in _ORIGIN_KIND_ORDER else 99)
    for p in parsed[:max_origins]:
        hit, kind = _resolve_one(p, resolvers)
        if not hit:
            continue
        text = str(hit.get("content") or hit.get("detail") or "").strip()
        if not text:
            continue
        title = str(hit.get("title") or "").strip()
        parts.append(f"【{title}】\n{text}" if title else text)
        hits.append(f"{kind}:{p['source_id']}")
    return "\n\n".join(parts)[:_MAX_SOURCE_TEXT], hits


def _snippet(text: str) -> str:
    """无 AI 时的降级详情：取第一个非空句（限长），不跨句拼接。"""
    for seg in text.replace("。", "。\n").split("\n"):
        seg = seg.strip(" 【】\n")
        if len(seg) >= 8:
            return seg[:_EXTRACT_SNIPPET]
    return text.strip()[:_EXTRACT_SNIPPET]


def backfill_entity_details(
    store: KnowledgeGraphStore,
    *,
    resolvers: Mapping[str, Callable[[str], dict[str, Any] | None]],
    ai_client: Any = None,
    dry_run: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """给缺失 detail 的存量实体回填详情。

    Args:
        store: 图存储（生产路径见 graph_store 路径解析）。
        resolvers: kind → 反查函数，返回 ``{"title": ..., "content": ...}``
            （content 为正文/摘要）。与 node_profile 的 resolvers 同族，但本
            函数需要正文而非仅标题。
        ai_client: 有则 LLM 归纳（detail_via="llm"）；无则原文首句截断
            （detail_via="extract"）。两者都写进 properties 供消费侧分辨。
        dry_run: 默认 True —— 只预览不落盘。实跑（改生产图谱文件）必须显式
            传 False。
        limit: 单批处理实体数上限（分批小步，防一次改太多）。

    Returns:
        ``{dry_run, total, missing, filled, skipped_no_source, failed, items}``；
        items 每项 ``{id, name, via, detail, sources}``（dry_run 时 detail 为
        预览值）。
    """
    entities = store.get_all_entities()
    missing = [e for e in entities if not str((e.get("properties") or {}).get("detail") or "").strip()]
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "total": len(entities),
        "missing": len(missing),
        "filled": 0,
        "skipped_no_source": 0,
        "failed": 0,
        "items": [],
    }

    for entity in missing[: max(1, int(limit))]:
        eid = str(entity.get("id") or "")
        name = str(entity.get("name") or "")
        text, hits = _gather_source_text(entity, resolvers)
        if not text:
            report["skipped_no_source"] += 1
            report["items"].append({"id": eid, "name": name, "via": "skipped", "detail": "", "sources": []})
            continue

        detail = ""
        via = "extract"
        if ai_client is not None and hasattr(ai_client, "complete_text"):
            try:
                reply = ai_client.complete_text(
                    [{"role": "user", "content": _DETAIL_PROMPT.format(name=name, text=text)}],
                    temperature=0.1,
                    max_tokens=200,
                )
                candidate = str(reply or "").strip()
                if candidate and candidate != "无":
                    detail = candidate[:300]
                    via = "llm"
            except Exception as exc:  # noqa: BLE001 — 单实体失败不阻断整批
                logger.warning("backfill: LLM 归纳失败 %s (%s): %s", eid, name, exc)
                report["failed"] += 1
                report["items"].append({"id": eid, "name": name, "via": "failed", "detail": "", "sources": hits})
                continue
        if not detail:
            detail = _snippet(text)
            via = "extract"
        if not detail:
            report["skipped_no_source"] += 1
            report["items"].append({"id": eid, "name": name, "via": "skipped", "detail": "", "sources": hits})
            continue

        report["items"].append({"id": eid, "name": name, "via": via, "detail": detail, "sources": hits})
        report["filled"] += 1
        if not dry_run:
            store.add_entity(
                eid,
                name,
                str(entity.get("type") or "unknown"),
                properties={"detail": detail, "detail_via": via, "detail_sources": hits},
            )

    if not dry_run and report["filled"]:
        store.save()
    return report
