# -*- coding: utf-8 -*-
"""知识归纳合成 — 把原始对话/实体"归纳"成可阅读的知识成果。

借鉴成熟竞品方法论落地（Obsidian MOC / Heptabase 原子卡片 / LLM Wiki 三层架构）：

- **原子知识卡片**：一段对话 → N 条"一卡一结论"的知识点（kernel 归纳，dsh 优先）。
- **主题 MOC**：把知识卡片按主题聚类，生成"主题地图"（含一句话总结、关联主题），
  并把知识图谱实体的分组从"对话 <uuid>"升级为有业务含义的领域主题。
- **知识简报**：把近期新增卡片整理成可直接阅读的 Markdown 简报。

数据层：卡片/MOC 都写入 ``structured_memory_entries``（与 mcp siyi_memory_store
共用同一键与记录形状），图谱实体仅更新 ``properties.topic`` 属性。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("siyi.knowledge_synthesis")

CARD_ENTITY_TYPE = "knowledge.card"
MOC_ENTITY_TYPE = "knowledge.moc"
BRIEF_ENTITY_TYPE = "knowledge.brief"
CARD_TAG = "知识卡片"
MOC_TAG = "知识地图"


# ── 内核文本生成（dsh 优先，失败回退 ai_client） ──────────────


def _kernel_text(context: Any, prompt: str, system: str, timeout_s: int = 240) -> str:
    from services.kernel_ai import call_ai_text, kernel_generate

    return kernel_generate(
        getattr(context, "agent_gateway", None),
        prompt,
        system=system,
        timeout_s=timeout_s,
        fallback=lambda: call_ai_text(getattr(context, "ai_client", None), prompt, system=system),
    )


def _extract_json_array(text: str):
    """从 LLM 返回文本中稳健地解析首个 JSON 数组（容忍代码围栏/前后缀文字）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        raw = "\n".join(lines).strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        logger.warning("知识归纳：LLM JSON 解析失败: %s", exc)
        return None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _conv_text(conv: dict[str, Any], limit: int = 2500) -> str:
    """从对话记录拼接可归纳文本，控制长度避免 Windows 命令行超限。"""
    parts = []
    for msg in conv.get("messages") or []:
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        role = msg.get("role") or ""
        if role == "user":
            parts.append(f"问：{content[:600]}")
        elif role == "assistant":
            parts.append(f"答：{content[:600]}")
        else:
            parts.append(content[:600])
        if sum(len(p) for p in parts) >= limit:
            break
    return " | ".join(parts)


# ── 1. 原子知识卡片 ─────────────────────────────────────────

_CARD_PROMPT = (
    "你是知识归纳助手。把下面这段 AI 对话内容，归纳成若干条「原子知识卡片」。\n"
    "每条卡片只记录一个独立、完整、可复用的知识点/结论。\n"
    "只输出 JSON 数组，格式如下（不要输出任何其他文字）：\n"
    '[{{"title":"卡片标题(<=12字)","conclusion":"一句话结论(<=60字，必须是陈述句)","topic":"所属主题(<=8字，如 一梦江湖脚本 / DeepSeek集成 / 定时任务)","entities":["关键实体名"]}}]\n'
    "要求：\n"
    "- 宁缺毋滥，只保留有价值的知识结论；寒暄、闲聊、重复内容一律丢弃\n"
    "- conclusion 必须是明确结论，不能是疑问句或半句话\n"
    "- 最多输出 6 张卡片\n"
    "- 只输出 JSON 数组\n\n"
    "对话内容：\n{text}\n"
)
_CARD_SYSTEM = "你是知识归纳助手，输出精炼的原子知识卡片 JSON，不要多余解释。"


def atomic_cards_from_conversation(context: Any, conv: dict[str, Any]) -> list[dict[str, Any]]:
    """把一段对话归纳成原子知识卡片并写入结构化记忆。

    Returns:
        本次新增的卡片列表（跳过已存在/无效卡片）。
    """
    store = getattr(context, "local_data_store", None)
    if store is None:
        return []
    text = _conv_text(conv)
    if not text:
        return []
    conv_id = str(conv.get("id") or conv.get("title") or "")

    prompt = _CARD_PROMPT.format(text=text[:2500])
    reply = _kernel_text(context, prompt, _CARD_SYSTEM)
    cards = _extract_json_array(reply)
    if not isinstance(cards, list):
        # X2 治理（2026-09-16，审计 D 批）：原为 info 级——内核未接通/返回格式
        # 漂移时整批对话零卡片且无人知晓（knowledge.synthesize 仅成功 1 次的
        # 同族根因）。失败必须 warning 留痕。
        logger.warning("知识卡片：对话 %s 未产出有效卡片（内核不可用或输出非 JSON 数组）", conv_id[:8])
        return []

    entries = store.load_json("structured_memory_entries", []) or []
    if not isinstance(entries, list):
        entries = []
    # 去重：同会话 + 同标题
    existing = {(str(e.get("_conv") or ""), str(e.get("title") or "")) for e in entries if e.get("entity_type") == CARD_ENTITY_TYPE}

    added: list[dict[str, Any]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        title = str(card.get("title") or "").strip()
        conclusion = str(card.get("conclusion") or "").strip()
        topic = str(card.get("card_topic") or card.get("topic") or "未分类").strip()[:12]
        if not title or not conclusion or (conv_id, title) in existing:
            continue
        digest = hashlib.md5(f"{conv_id}|{title}".encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
        entities = [str(e).strip() for e in (card.get("entities") or []) if str(e).strip()]
        now = _now()
        entry = {
            "id": secrets.token_hex(8),
            "supertag_type": "@知识",
            "entity_type": CARD_ENTITY_TYPE,
            "relations": [],
            "source": "deepseek",
            "created_at": now,
            "updated_at": now,
            "tags": [CARD_TAG],
            "content": conclusion,
            "title": title[:40],
            "card_topic": topic,
            "entities": entities,
            "_conv": conv_id,
            "_digest": digest,
        }
        entries.append(entry)
        added.append(entry)
        existing.add((conv_id, title))

    if added:
        store.save_json("structured_memory_entries", entries)
        logger.info("知识卡片：对话 %s 新增 %d 张卡片", conv_id[:8], len(added))
    return added


# ── 读取知识卡片 ────────────────────────────────────────────


def list_knowledge_cards(context: Any, days: int = 0) -> list[dict[str, Any]]:
    """读取知识卡片（可按最近 N 天过滤）。"""
    store = getattr(context, "local_data_store", None)
    if store is None:
        return []
    entries = store.load_json("structured_memory_entries", []) or []
    if not isinstance(entries, list):
        return []
    cards = [e for e in entries if isinstance(e, dict) and e.get("entity_type") == CARD_ENTITY_TYPE]
    if days and days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cards = [e for e in cards if str(e.get("created_at") or "") >= cutoff]
    # 时间倒序
    cards.sort(key=lambda e: str(e.get("created_at") or ""), reverse=True)
    return cards


# ── 2. 主题 MOC 归纳 ────────────────────────────────────────


def _short_of_topic(topic_short: str) -> str:
    """从「对话 <short>」中取出 8 位 short；非该格式原样返回。"""
    m = re.search(r"对话\s+([0-9a-fA-F]{8})", topic_short or "")
    return m.group(1) if m else ""


def apply_card_topics_to_graph(context: Any) -> int:
    """把知识卡片的领域主题回写到知识图谱实体的 topic 属性。

    将实体的「对话 <uuid8>」分组映射为卡片主题（一对话多主题时取最常用）。
    无卡片对应的实体保持原样。返回更新实体数。
    """
    kg = getattr(context, "knowledge_graph_extractor", None)
    store = getattr(kg, "store", None)
    if store is None:
        return 0
    cards = list_knowledge_cards(context, days=0)
    # conv(前8) -> topic（计数取多数）
    conv_topics: dict[str, dict[str, int]] = {}
    for c in cards:
        cid = str(c.get("_conv") or "")
        topic = str(c.get("card_topic") or "").strip()
        if not cid or not topic:
            continue
        key = cid[:8]
        counts = conv_topics.setdefault(key, {})
        counts[topic] = counts.get(topic, 0) + 1
    best = {k: max(v.items(), key=lambda x: x[1])[0] for k, v in conv_topics.items()}
    if not best:
        return 0

    changed = 0
    for ent in store.get_all_entities():
        eid = ent.get("id", "")
        if eid.startswith("mem_"):
            continue
        props = ent.get("properties") or {}
        old_topic = str(props.get("topic") or "")
        short = _short_of_topic(old_topic)
        new_topic = best.get(short)
        if new_topic and new_topic != old_topic:
            store.add_entity(eid, ent.get("name", ""), ent.get("type", "concept"), properties={"topic": new_topic})
            changed += 1
    if changed:
        store.save()
        logger.info("知识图谱：已将 %d 个实体主题从对话分组升级为领域主题", changed)
    return changed


_MOC_PROMPT = (
    "下面是按主题聚类的知识卡片（每行「主题｜卡片标题｜结论」）。\n"
    "请为这些主题生成「知识地图 MOC」：每个主题给出一句话总结，并列出与之相关的其它主题。\n"
    "只输出 JSON 数组，格式如下（不要任何其它文字）：\n"
    '[{{"topic":"主题名","summary":"一句话总结(<=60字)","related":["相关主题名"]}}]\n'
    "要求：\n"
    "- 主题名必须与输入完全一致，不要改名\n"
    "- summary 概括该主题下所有卡片的共同知识\n"
    "- related 从输入中出现过的主题里挑选，最多 3 个\n\n"
    "卡片列表：\n{payload}\n"
)
_MOC_SYSTEM = "你是知识管理专家，输出精炼的主题地图 MOC JSON，不要多余解释。"


def build_topic_mocs(context: Any, max_topics: int = 12) -> dict[str, Any]:
    """把知识卡片按主题聚类，生成主题 MOC（含总结/关联），落结构化记忆。

    Returns:
        {"mocs": [...], "topics": n, "cards": n}
    """
    store = getattr(context, "local_data_store", None)
    if store is None:
        return {"mocs": [], "topics": 0, "cards": 0}
    cards = list_knowledge_cards(context, days=0)
    if not cards:
        return {"mocs": [], "topics": 0, "cards": 0}

    # 聚类：topic -> [card]
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for c in cards:
        t = str(c.get("card_topic") or "未分类")
        by_topic.setdefault(t, []).append(c)
    topics_sorted = sorted(by_topic.items(), key=lambda kv: -len(kv[1]))[:max_topics]

    payload_lines = []
    for topic, cs in topics_sorted:
        for c in cs[:4]:  # 每主题最多带 4 张卡片控制长度
            title = str(c.get("title") or "")
            concl = str(c.get("content") or "")[:50]
            payload_lines.append(f"{topic}｜{title}｜{concl}")
    payload = "\n".join(payload_lines)

    prompt = _MOC_PROMPT.format(payload=payload[:3500])
    reply = _kernel_text(context, prompt, _MOC_SYSTEM)
    parsed = _extract_json_array(reply)
    if not isinstance(parsed, list):
        logger.warning("知识 MOC：内核未产出有效 MOC，仅做主题分组落库")
        parsed = []

    # topic 名 -> MOC 数据（保序、只保留出现在输入中的主题）
    moc_map: dict[str, dict[str, Any]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "").strip()
        if topic in by_topic:
            moc_map[topic] = item

    entries = store.load_json("structured_memory_entries", []) or []
    if not isinstance(entries, list):
        entries = []
    # 去重：同主题 MOC 已存在则更新，否则追加
    now = _now()
    mocs: list[dict[str, Any]] = []
    for topic, cs in topics_sorted:
        summary = str((moc_map.get(topic) or {}).get("summary") or "").strip()
        related = [str(x) for x in ((moc_map.get(topic) or {}).get("related") or []) if str(x).strip()][:3]
        card_ids = [c.get("id") for c in cs if c.get("id")]
        content_parts = []
        if summary:
            content_parts.append(summary)
        content_parts.append("卡片：")
        content_parts.extend(f"- {str(c.get('title') or '')}: {str(c.get('content') or '')[:60]}" for c in cs[:8])
        entry = {
            "id": secrets.token_hex(8),
            "supertag_type": "@知识",
            "entity_type": MOC_ENTITY_TYPE,
            "relations": [{"type": "topic_related", "target_id": r} for r in related],
            "source": "knowledge.synthesize",
            "created_at": now,
            "updated_at": now,
            "tags": [MOC_TAG],
            "title": topic[:30],
            "card_topic": topic,
            "content": "\n".join(content_parts),
            "card_ids": card_ids,
            "related_topics": related,
            "card_count": len(cs),
            "_digest": hashlib.md5(f"moc|{topic}".encode("utf-8"), usedforsecurity=False).hexdigest()[:12],
        }
        # 替换旧 MOC（按 _digest）
        idx = next((i for i, e in enumerate(entries) if e.get("entity_type") == MOC_ENTITY_TYPE and e.get("_digest") == entry["_digest"]), None)
        if idx is not None:
            entries[idx] = entry
        else:
            entries.append(entry)
        mocs.append(entry)

    store.save_json("structured_memory_entries", entries)

    # 回写图谱实体主题（无 LLM，按卡片主题映射）
    try:
        graph_changed = apply_card_topics_to_graph(context)
    except Exception as exc:  # noqa: BLE001
        logger.warning("知识 MOC：回写图谱主题失败（非阻塞）: %s", exc)
        graph_changed = 0

    logger.info("知识 MOC：生成 %d 个主题地图（%d 张卡片），回写图谱 %d 实体", len(mocs), len(cards), graph_changed)
    return {"mocs": mocs, "topics": len(topics_sorted), "cards": len(cards)}


def list_topic_mocs(context: Any) -> list[dict[str, Any]]:
    """读取主题 MOC。"""
    store = getattr(context, "local_data_store", None)
    if store is None:
        return []
    entries = store.load_json("structured_memory_entries", []) or []
    if not isinstance(entries, list):
        return []
    mocs = [e for e in entries if isinstance(e, dict) and e.get("entity_type") == MOC_ENTITY_TYPE]
    mocs.sort(key=lambda e: int(e.get("card_count") or 0), reverse=True)
    return mocs


# ── 3. 知识简报 ─────────────────────────────────────────────

_BRIEF_PROMPT = (
    "下面是最近新增的知识卡片（每行「主题｜卡片标题｜结论」）。\n"
    "请生成一份可读的中文「知识简报」，用 Markdown 格式：\n"
    "# 知识简报（{period}）\n"
    "## 核心要点\n- 用 2-3 条一句话概括本期最重要的知识点\n"
    "## 主题速览\n- 每个主题一行：主题名：一句话概括该主题学到了什么\n"
    "## 待深入\n- 如发现卡片间存在未闭环/互相矛盾的结论，列出 1-2 条建议深入研究的方向；没有则写「暂无」\n"
    "不要出现「根据卡片」之类废话，直接给出结论。\n\n"
    "卡片列表：\n{payload}\n"
)
_BRIEF_SYSTEM = "你是知识管理专家，输出精炼的中文 Markdown 知识简报。"


def generate_knowledge_brief(context: Any, days: int = 7) -> dict[str, Any]:
    """生成最近 N 天新增知识的可读简报，落结构化记忆并返回 Markdown。"""
    store = getattr(context, "local_data_store", None)
    if store is None:
        return {"success": False, "error": "data_store not configured"}
    cards = list_knowledge_cards(context, days=days)
    if not cards:
        return {"success": False, "cards": 0, "error": "最近无新增知识卡片"}

    payload_lines = []
    for c in cards[:40]:
        topic = str(c.get("card_topic") or "未分类")
        title = str(c.get("title") or "")
        concl = str(c.get("content") or "")[:60]
        payload_lines.append(f"{topic}｜{title}｜{concl}")
    payload = "\n".join(payload_lines)

    period = f"最近{days}天" if days else "全部"
    prompt = _BRIEF_PROMPT.format(period=period, payload=payload[:3500])
    markdown = _kernel_text(context, prompt, _BRIEF_SYSTEM).strip()
    if not markdown:
        return {"success": False, "cards": len(cards), "error": "内核未产出简报"}

    now = _now()
    entry = {
        "id": secrets.token_hex(8),
        "supertag_type": "@知识",
        "entity_type": BRIEF_ENTITY_TYPE,
        "relations": [],
        "source": "knowledge.brief",
        "created_at": now,
        "updated_at": now,
        "tags": ["知识简报"],
        "title": f"知识简报 {now[:10]}",
        "content": markdown,
        "card_count": len(cards),
    }
    entries = store.load_json("structured_memory_entries", []) or []
    if not isinstance(entries, list):
        entries = []
    entries.append(entry)
    store.save_json("structured_memory_entries", entries)
    logger.info("知识简报：已生成（%d 张卡片）", len(cards))
    return {"success": True, "markdown": markdown, "cards": len(cards), "entry_id": entry["id"]}
