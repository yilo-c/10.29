# -*- coding: utf-8 -*-
"""知识库 MD 反向同步 — 改写 MD → 回灌记忆/图谱（阶段三）。

用户在前端「知识库」模块（或任何编辑器）改写 MD 后，``knowledge.md_save`` /
``knowledge.md_sync`` 会调用本模块把改写内容回灌：

- 依据 frontmatter ``type`` 决定回灌目标：
  - ``知识卡片``  → 更新/新建 ``knowledge.card`` 记忆条目（保留原 id/来源）
  - ``知识地图``  → 更新 ``knowledge.moc``（MOC 总结/关联主题）
  - ``记忆``      → 按 ``_md_rel`` 幂等更新普通记忆条目
  - ``当日索引``  → 提取「## 今日速记」小节写入「今日速记 <date>」记忆条目
  - ``对话/资讯摘要/资讯条目/图谱快照`` → 派生文件，跳过回灌
- 数据层与 mcp ``siyi_memory_store`` 共用 ``structured_memory_entries`` 同键同形；
- 图谱回灌：正文实体经 LLM 提取写入知识图谱，并打上 frontmatter 主题属性
  （无内核/失败时**跳过提取**，不回落正则，避免噪音——项目硬约束）。

幂等：以 ``_md_rel``（或卡片/MOC 的 topic+title 唯一键）定位既有条目，
重复保存同一文件只会更新而不会重复新增。
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime
from typing import Any

from knowledge.md_store import parse_frontmatter

logger = logging.getLogger("siyi.knowledge.md_parser")

CARD_ENTITY_TYPE = "knowledge.card"
MOC_ENTITY_TYPE = "knowledge.moc"
CARD_TAG = "知识卡片"
MOC_TAG = "知识地图"

# 派生文件类型：由原始数据生成，改写仅保留 MD 结果，不回灌
SYNC_SKIP_TYPES = {"对话", "资讯摘要", "资讯条目", "图谱快照"}

_ENTITY_LINE_RE = re.compile(r"\*\*关键实体\*\*\s*[:：]\s*([^\n]+)")
_DAILY_NOTE_RE = re.compile(r"##\s*今日速记\s*\n+(.*)$", re.S)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_text(text: str) -> str:
    """去掉正文里的 Markdown 标记噪音，返回可入库的纯文本。"""
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text or "")  # 图片
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)  # 链接 → 文字
    s = re.sub(r"[#>*`~|]", "", s)  # 标记符号
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _strip_label(body: str, label: str) -> str:
    """从 ``**结论**：xxx`` 这类行里取冒号后的内容（截到下一个二级标签或空行）。"""
    marker = "**%s**" % label
    idx = (body or "").find(marker)
    if idx == -1:
        return ""
    rest = body[idx + len(marker) :].lstrip("：: \n")
    # 截到下一个 `**标签**` 或空行，避免把后续字段吞进来
    rest = re.split(r"\n\s*\n|\n\s*\*\*", rest, maxsplit=1)[0]
    return rest.strip()


def _parse_entity_line(body: str) -> list[str]:
    m = _ENTITY_LINE_RE.search(body or "")
    if not m:
        return []
    return [x.strip() for x in re.split(r"[、,，;；]", m.group(1)) if x.strip()][:8]


def _extract_daily_note(body: str) -> str:
    m = _DAILY_NOTE_RE.search(body or "")
    if not m:
        return ""
    note = re.sub(r"^>\s*", "", m.group(1), flags=re.M)  # 去掉引用符
    note = re.sub(r"^- ", "", note, flags=re.M)  # 去列表符
    note = _clean_text(note)
    return note[:800]


def _upsert(
    entries: list[dict[str, Any]],
    entity_type: str,
    new_entry: dict[str, Any],
    *,
    key: tuple[str, ...],
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """在 entries 中按 ``entity_type + key`` 定位并原地更新；未命中则追加。

    Returns:
        (entries, entry, action)  action ∈ {"update", "create"}
    """
    created_at = new_entry.get("created_at") or _now()
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or e.get("entity_type") != entity_type:
            continue
        if all(e.get(k) == new_entry.get(k) for k in key):
            # 保留原 id / created_at / source / _conv 等指纹字段
            keep = {k: e.get(k) for k in ("id", "created_at", "source", "_conv", "_digest") if e.get(k) is not None}
            merged = {**new_entry, **keep, "updated_at": _now(), "created_at": e.get("created_at") or created_at}
            entries[i] = merged
            return entries, merged, "update"
    new_entry["id"] = new_entry.get("id") or secrets.token_hex(8)
    new_entry.setdefault("created_at", created_at)
    new_entry["updated_at"] = _now()
    entries.append(new_entry)
    return entries, new_entry, "create"


def _build_card_entry(meta: dict[str, Any], body: str) -> dict[str, Any]:
    conclusion = _strip_label(body, "结论") or _clean_text(body)[:300]
    topic = str(meta.get("topic") or "未分类").strip()[:12]
    tags = [str(t) for t in (meta.get("tags") or []) if str(t).strip()] or [CARD_TAG]
    title = str(meta.get("title") or "").strip()[:40]
    entities = _parse_entity_line(body)
    return {
        "supertag_type": "@知识",
        "entity_type": CARD_ENTITY_TYPE,
        "relations": [],
        "source": str(meta.get("source") or "md_parser"),
        "tags": tags,
        "content": conclusion,
        "title": title or "未命名卡片",
        "card_topic": topic,
        "entities": entities,
        "created_at": str(meta.get("date") or _now()),
    }


def _build_moc_entry(meta: dict[str, Any], body: str) -> dict[str, Any]:
    topic = str(meta.get("topic") or meta.get("title") or "未分类").strip()[:30]
    content = _clean_text(body)[:600]
    related = [str(t) for t in (meta.get("related_topics") or meta.get("related") or []) if str(t).strip()][:3]
    return {
        "supertag_type": "@知识",
        "entity_type": MOC_ENTITY_TYPE,
        "relations": [{"type": "topic_related", "target_id": r} for r in related],
        "source": "md_parser",
        "tags": [MOC_TAG],
        "title": topic,
        "card_topic": topic,
        "content": content or "（暂无总结）",
        "related_topics": related,
        "created_at": str(meta.get("date") or _now()),
    }


def _build_note_entry(meta: dict[str, Any], body: str, title_hint: str = "") -> dict[str, Any]:
    title = str(meta.get("title") or "").strip() or title_hint or "手动笔记"
    return {
        "supertag_type": "@知识",
        "entity_type": "memory",
        "relations": [],
        "source": "md_parser",
        "tags": [str(t) for t in (meta.get("tags") or []) if str(t).strip()] or ["手动笔记"],
        "title": title[:40],
        "content": _clean_text(body)[:1000] or "（空笔记）",
        "created_at": str(meta.get("date") or _now()),
    }


# ── 图谱回灌 ────────────────────────────────────────────────────────


def _sync_entities_to_graph(
    context: Any,
    body: str,
    source_id: str,
    topic: str,
) -> dict[str, Any]:
    """把正文实体写入知识图谱并打主题。

    尊重项目硬约束：仅当有内核/ai_client 时才走 LLM 提取；否则跳过，
    不回落正则（正则提取会制造大量句子碎片噪音）。
    """
    extractor = getattr(context, "knowledge_graph_extractor", None)
    ai = getattr(context, "ai_client", None)
    if extractor is None or ai is None or not (body or "").strip():
        return {"extracted": 0, "reason": "no_ai_client_or_empty"}

    try:
        result = extractor.extract_from_text(body, source_id=source_id, use_llm=True, source_kind="memory")
    except Exception as exc:  # noqa: BLE001
        logger.warning("MD 回灌图谱：实体提取失败（跳过，不回落正则）: %s", exc)
        return {"extracted": 0, "reason": "extract_failed"}

    entities = result.get("entities") or []
    # 给提取出的实体打上主题属性（无主题则跳过）
    store = getattr(extractor, "store", None)
    topic = (topic or "").strip()
    if store is not None and topic:
        try:
            for e in entities:
                eid = e.get("id")
                if not eid:
                    continue
                store.add_entity(
                    eid,
                    str(e.get("name") or ""),
                    str(e.get("type") or "concept"),
                    properties={"topic": topic},
                )
            store.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("MD 回灌图谱：打主题失败（非阻塞）: %s", exc)
    return {"extracted": len(entities), "topic": topic}


# ── 对外入口 ────────────────────────────────────────────────────────


def sync_md_to_backend(
    context: Any,
    rel: str,
    content: str,
) -> dict[str, Any]:
    """把（用户改写后的）MD 文件内容回灌到记忆/图谱。

    Args:
        context: 至少带 ``local_data_store``；可选 ``knowledge_graph_extractor`` / ``ai_client``。
        rel: 知识库内相对路径（用于幂等定位）。
        content: 完整 MD 内容（含 frontmatter）。

    Returns:
        {success, rel, type, action, entry_id, graph, skipped?}
    """
    meta, body = parse_frontmatter(content or "")
    mtype = str(meta.get("type") or "").strip()
    topic = str(meta.get("topic") or "").strip()

    if mtype in SYNC_SKIP_TYPES:
        return {
            "success": True,
            "rel": rel,
            "type": mtype or "未知",
            "skipped": True,
            "reason": "派生文件类型，不回灌",
        }

    store = getattr(context, "local_data_store", None)
    if store is None:
        return {"success": False, "error": "local_data_store not configured", "rel": rel}

    entries = store.load_json("structured_memory_entries", [])
    if not isinstance(entries, list):
        entries = []

    result: dict[str, Any] = {"success": True, "rel": rel, "type": mtype or "记忆"}
    entry: dict[str, Any] | None = None

    if mtype == "知识卡片":
        new_entry = _build_card_entry(meta, body)
        new_entry["_md_rel"] = rel
        entries, entry, action = _upsert(
            entries,
            CARD_ENTITY_TYPE,
            new_entry,
            key=("card_topic", "title"),
        )
    elif mtype == "知识地图":
        new_entry = _build_moc_entry(meta, body)
        new_entry["_md_rel"] = rel
        entries, entry, action = _upsert(
            entries,
            MOC_ENTITY_TYPE,
            new_entry,
            key=("card_topic",),
        )
    elif mtype == "当日索引":
        note = _extract_daily_note(body)
        if not note:
            return {
                "success": True,
                "rel": rel,
                "type": mtype,
                "skipped": True,
                "reason": "今日速记为空白",
            }
        new_entry = _build_note_entry(meta, note, title_hint=f"今日速记 {str(meta.get('date') or _now())[:10]}")
        new_entry["_md_rel"] = rel
        entries, entry, action = _upsert(
            entries,
            "memory",
            new_entry,
            key=("_md_rel",),
        )
    else:  # 记忆 / 其他 → 通用笔记（按 rel 幂等）
        new_entry = _build_note_entry(meta, body)
        new_entry["_md_rel"] = rel
        entries, entry, action = _upsert(
            entries,
            "memory",
            new_entry,
            key=("_md_rel",),
        )

    if entry is None:
        return {**result, "action": "skip", "reason": "no_entry"}

    store.save_json("structured_memory_entries", entries)
    result.update({"action": action, "entry_id": entry.get("id")})

    # 图谱回灌：正文实体提取（LLM，有内核才做）
    graph_result = _sync_entities_to_graph(context, body, str(entry.get("id") or ""), topic)
    result["graph"] = graph_result

    logger.info(
        "MD 反向同步完成: %s type=%s action=%s entry=%s graph=%s",
        rel,
        mtype or "记忆",
        action,
        entry.get("id", "")[:8],
        graph_result.get("extracted", 0),
    )
    return result


__all__ = ["sync_md_to_backend"]
