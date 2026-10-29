# -*- coding: utf-8 -*-
"""知识库 MD 导出器 — 把当前沉淀的数据按时间/主题导出为可读可改的 Markdown。

对应《知识体系 MD 化方案》：
- 内容层 = ``<data_base_dir>/knowledge/`` 下的 MD 文件（事实源）；
- 时间轴 = ``YYYY/YYYY-MM/YYYY-MM-DD_<类别>.md``（资讯摘要/对话/记忆/知识卡片/当日索引）；
- 主题轴 = ``主题/<主题>/MOC.md`` + ``卡片-<slug>.md``；
- 索引层 = ``00_索引/``（README + 主题总览）+ ``图谱导出/knowledge_graph.md`` 快照。

本模块只导出「归纳过的知识」：
- ``structured_memory_entries``（info.digest → 资讯摘要；knowledge.card → 卡片；
  knowledge.moc → MOC；其余 → 记忆）
- ``chat_sessions``（本项目对话，过滤空会话/测试会话）
- ``knowledge_graph.json``（图谱快照）
原始资讯流、运维日志等仍留在原存储，不落 MD（保信噪比）。

导出是幂等的：每次从当前数据全量重建知识库文件。
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from knowledge.md_store import (
    MDKnowledgeStore,
    date_rel_path,
    extract_date,
    render_frontmatter,
    slugify,
)

logger = logging.getLogger("siyi.knowledge.md_exporter")

# 会话消息渲染上限（防止单个会话文件过大）
_MAX_MESSAGES_PER_SESSION = 40
_MAX_CHARS_PER_MESSAGE = 300


def _fmt_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _label_of_speaker(speaker: str) -> str:
    s = str(speaker or "").strip()
    if s in ("你", "user", "human", "用户"):
        return "问"
    if s in ("AI", "assistant", "bot", "agent", "助手"):
        return "答"
    return s or "消息"


def _render_conversation_messages(messages: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for msg in messages[-_MAX_MESSAGES_PER_SESSION:]:
        speaker = str(msg.get("speaker") or msg.get("role") or "").strip()
        content = str(msg.get("message") or msg.get("content") or "").strip()
        if not content:
            continue
        if len(content) > _MAX_CHARS_PER_MESSAGE:
            content = content[:_MAX_CHARS_PER_MESSAGE] + "…"
        lines.append(f"- **{_label_of_speaker(speaker)}**：{content}")
    return lines


def _load_graph(context: Any):
    """返回 (entities, relations)；图谱不可用时返回空列表对。"""
    kg = getattr(context, "knowledge_graph_store", None)
    if kg is None:
        return [], []
    try:
        return kg.get_all_entities(), kg.get_all_relations()
    except Exception as exc:  # noqa: BLE001
        logger.warning("知识图谱快照读取失败（非阻塞）: %s", exc)
        return [], []


# ── 1. 结构化记忆 → 时间轴/主题轴文件 ───────────────────────────────

def _export_memory(
    context: Any,
    files: dict[str, str],
    stats: dict[str, Any],
    *,
    min_date: tuple[int, int, int] | None = None,
    include_cards: bool = True,
) -> None:
    store = getattr(context, "local_data_store", None)
    if store is None:
        return
    entries = store.load_json("structured_memory_entries", [])
    if not isinstance(entries, list):
        entries = []

    by_date_memory: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    by_date_digest: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    card_files: dict[str, str] = {}   # rel -> content（同主题同 slug 去重）
    moc_files: dict[str, str] = {}
    card_count = 0
    moc_count = 0

    for e in entries:
        if not isinstance(e, dict):
            continue
        et = str(e.get("entity_type") or "").strip()
        created = extract_date(e.get("created_at") or "")
        title = str(e.get("title") or "").strip() or "未命名记忆"
        content = str(e.get("content") or "").strip()

        if et == "info.digest":
            if created is None or not content:
                continue
            if min_date is not None and created < min_date:
                continue
            by_date_digest[created].append(content)
            stats["digests"] += 1
            continue

        if et == "knowledge.card":
            if not include_cards:
                continue
            topic = str(e.get("card_topic") or "未分类").strip()
            rel = f"主题/{topic}/卡片-{slugify(title)}.md"
            if rel in card_files:
                continue  # 同主题同标题去重
            tags = [str(t) for t in (e.get("tags") or []) if str(t).strip()]
            meta = {
                "title": title[:40],
                "date": str(e.get("created_at") or _fmt_now())[:10],
                "type": "知识卡片",
                "topic": topic,
                "tags": tags,
                "source": str(e.get("source") or ""),
                "related": [f"MOC-{topic}"],
                "auto": "true",
            }
            body = f"**结论**：{content}\n"
            entities = [str(x) for x in (e.get("entities") or []) if str(x).strip()]
            if entities:
                body += f"\n**关键实体**：{'、'.join(entities[:8])}\n"
            detail = str(e.get("detail") or "").strip()
            expansion = str(e.get("expansion") or "").strip()
            analysis = str(e.get("analysis") or "").strip()
            if detail:
                body += f"\n**详细解释**：{detail}\n"
            if expansion:
                body += f"\n**相关拓展**：{expansion}\n"
            if analysis:
                body += f"\n**再分析**：{analysis}\n"
            card_files[rel] = render_frontmatter(meta) + body
            card_count += 1
            continue

        if et == "knowledge.moc":
            if not include_cards:
                continue
            topic = str(e.get("card_topic") or title or "未分类").strip()
            rel = f"主题/{topic}/MOC.md"
            meta = {
                "title": topic[:30],
                "date": str(e.get("created_at") or _fmt_now())[:10],
                "type": "知识地图",
                "topic": topic,
                "tags": [str(t) for t in (e.get("tags") or []) if str(t).strip()],
                "auto": "true",
            }
            moc_files[rel] = render_frontmatter(meta) + (content or "（暂无总结）") + "\n"
            moc_count += 1
            continue

        if created is None:
            continue
        if min_date is not None and created < min_date:
            continue
        tags_str = " / ".join(str(t) for t in (e.get("tags") or [])[:5]) if e.get("tags") else et
        by_date_memory[created].append(
            f"- **{title[:40]}**（{tags_str or et}）：{content[:200]}"
        )
        stats["memories"] += 1

    # 落盘：资讯摘要（按日聚合）
    for d, blocks in by_date_digest.items():
        rel = date_rel_path(*d, "资讯摘要")
        body = "\n\n---\n\n".join(blocks)
        meta = {
            "date": f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}",
            "type": "资讯摘要",
            "source": "info.collect",
            "auto": "true",
        }
        files[rel] = render_frontmatter(meta) + body + "\n"
        stats["digest_files"] += 1

    # 落盘：记忆（按日聚合）
    for d, lines in by_date_memory.items():
        rel = date_rel_path(*d, "记忆")
        meta = {
            "date": f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}",
            "type": "记忆",
            "auto": "true",
        }
        files[rel] = render_frontmatter(meta) + "\n".join(lines) + "\n"
        stats["memory_files"] += 1

    # 落盘：卡片 + MOC
    files.update(card_files)
    files.update(moc_files)
    stats["cards"] = card_count
    stats["mocs"] = moc_count


# ── 2. 每日资讯条目 → 时间轴资讯文件 ───────────────────────────────

def _export_info_items(
    context: Any,
    files: dict[str, str],
    stats: dict[str, Any],
    *,
    per_day: int = 15,
    max_days: int = 0,
    min_date: tuple[int, int, int] | None = None,
) -> None:
    """把 info_item_repository 的每日资讯条目按日归档进 MD。

    原始资讯流本不该全部落库，但用户要求「每日的咨询」也进知识体系，
    因此按日挑选高价值（score）条目写入 ``YYYY-MM-DD_资讯条目.md``。
    """
    repo = getattr(context, "info_item_repository", None)
    if repo is None or not hasattr(repo, "list_items"):
        return
    try:
        items, total = repo.list_items(limit=20000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("资讯条目读取失败（非阻塞）: %s", exc)
        return

    by_date: dict[tuple[int, int, int], list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    for it in items:
        if not isinstance(it, dict):
            continue
        d = extract_date(it.get("collected_at") or it.get("created_at") or "")
        if d is None:
            continue
        if min_date is not None and d < min_date:
            continue
        try:
            score = float(it.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        by_date[d].append((score, it))

    dates = sorted(by_date.keys(), reverse=True)
    if max_days:
        dates = dates[:max_days]

    exported = 0
    for d in dates:
        scored = sorted(by_date[d], key=lambda x: x[0], reverse=True)[:per_day]
        lines: list[str] = []
        for score, it in scored:
            title = str(it.get("title_zh") or it.get("title") or "未命名").strip()[:80]
            summary = str(it.get("summary_zh") or it.get("summary") or "").strip()
            if len(summary) > 120:
                summary = summary[:120] + "…"
            source_label = str(it.get("source_label") or it.get("source") or "").strip()
            category = str(it.get("category") or "").strip()
            url = str(it.get("url") or "").strip()
            tag = f"[{category}]" if category else ""
            score_txt = f"（评分 {score:.1f}）" if score > 0 else ""
            line = f"- **{title}** {score_txt} {tag} 来自 {source_label}"
            if summary:
                line += f"\n  - {summary}"
            if url:
                line += f"\n  - {url}"
            lines.append(line)
        if not lines:
            continue
        rel = date_rel_path(*d, "资讯条目")
        meta = {
            "date": f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}",
            "type": "资讯条目",
            "source": "info.items",
            "auto": "true",
        }
        files[rel] = render_frontmatter(meta) + "\n".join(lines) + "\n"
        exported += 1
    stats["info_item_days"] = exported
    stats["info_items_total"] = total


# ── 3. 本项目对话 → 时间轴对话文件 ─────────────────────────────────

def _export_conversations(
    context: Any,
    files: dict[str, str],
    stats: dict[str, Any],
    max_conversations: int,
    *,
    min_date: tuple[int, int, int] | None = None,
) -> None:
    chat = getattr(context, "chat_session_repository", None)
    if chat is None or not hasattr(chat, "list_sessions"):
        return
    try:
        sessions = chat.list_sessions()
    except Exception as exc:  # noqa: BLE001
        logger.warning("会话列表读取失败（非阻塞）: %s", exc)
        return

    by_date: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    exported = 0
    for s in sessions:
        if max_conversations and exported >= max_conversations:
            break
        sid = s.get("id")
        title = str(s.get("title") or "").strip() or f"会话 {sid}"
        updated = str(s.get("updated_at") or s.get("created_at") or "")
        d = extract_date(updated)
        if d is None:
            continue
        if min_date is not None and d < min_date:
            continue
        try:
            messages = chat.get_messages(sid) if hasattr(chat, "get_messages") else []
        except Exception:  # noqa: BLE001
            messages = []
        rendered = _render_conversation_messages(messages)
        if not rendered:
            continue  # 空会话/测试会话：无有效消息
        n = len(messages)
        block = [f"### {title[:50]}", f"> {updated[:10]} · {n} 条消息", ""]
        block.extend(rendered)
        block.append("")
        by_date[d].extend(block)
        exported += 1

    for d, lines in by_date.items():
        rel = date_rel_path(*d, "对话")
        meta = {
            "date": f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}",
            "type": "对话",
            "source": "chat_sessions",
            "auto": "true",
        }
        files[rel] = render_frontmatter(meta) + "\n".join(lines) + "\n"
        stats["conversation_files"] += 1
    stats["conversations"] = exported


# ── 3. 知识图谱 → 快照 ─────────────────────────────────────────────

def _export_graph(
    context: Any,
    files: dict[str, str],
    stats: dict[str, Any],
    max_entities: int,
) -> None:
    entities, relations = _load_graph(context)
    if not entities:
        return
    valid = [
        e for e in entities
        if str(e.get("name") or "").strip() and not str(e.get("id") or "").startswith("mem_")
    ]
    # 优先保留带主题的实体，再按名称长度过滤碎片
    valid.sort(key=lambda e: (str((e.get("properties") or {}).get("topic") or "") != "", -len(str(e.get("name") or ""))))
    valid = valid[:max_entities]

    rel_count = len(relations) if relations else 0

    name_map = {e.get("id"): str(e.get("name") or "") for e in entities}
    rel_lines = []
    for r in (relations or [])[:800]:
        src = name_map.get(r.get("source_id"), str(r.get("source_id")))
        dst = name_map.get(r.get("target_id"), str(r.get("target_id")))
        rtype = str(r.get("relation_type") or r.get("type") or "related_to")
        rel_lines.append(f"- {src} ─{rtype}→ {dst}")
    rel_body = "\n".join(rel_lines) if rel_lines else "（暂无语义关系）"

    header = [
        "# 知识图谱快照",
        "",
        f"> 由 knowledge_graph.json 自动导出 · {_fmt_now()} · 全库 {len(entities)} 实体 / {rel_count} 关系",
        f"> 本页仅列 {len(valid)} 个经过滤的实体（剔除记忆锚点/碎片），完整数据以图谱视图为准。",
        "",
        "## 实体清单",
        "",
        "| 名称 | 类型 | 主题 |",
        "|------|------|------|",
    ]
    for e in valid:
        name = str(e.get("name") or "")[:40].replace("|", "｜")
        etype = str(e.get("type") or "concept")
        topic = str((e.get("properties") or {}).get("topic") or "")
        header.append(f"| {name} | {etype} | {topic} |")
    header.extend(["", "## 关系清单", ""])
    header.append(rel_body)
    header.append("")

    rel = "图谱导出/knowledge_graph.md"
    meta = {"title": "知识图谱快照", "date": _fmt_now()[:10], "type": "图谱快照", "auto": "true"}
    files[rel] = render_frontmatter(meta) + "\n".join(header)
    stats["graph_entities"] = len(valid)
    stats["graph_relations"] = rel_count


# ── 4. 当日索引 + 全局索引 ─────────────────────────────────────────

def _build_daily_index(files: dict[str, str]) -> None:
    """为每个出现过的日期生成「当日索引」（Obsidian Daily Note 形态）。"""
    by_date: dict[str, list[str]] = defaultdict(list)
    for rel in files:
        if not rel.endswith(".md"):
            continue
        d = extract_date(rel)
        if d is None:
            continue
        key = f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}"
        by_date[key].append(rel)

    for date_key, rels in by_date.items():
        links = []
        for rel in sorted(rels):
            if rel.endswith(f"{date_key}_当日索引.md"):
                continue
            kind = rel.rsplit("_", 1)[-1].removesuffix(".md")
            # 当日索引与当日文件在同一日期目录，用 basename 相对链接
            links.append(f"- [{kind}]({os.path.basename(rel)})")
        if not links:
            continue
        body = [
            f"# {date_key} 当日索引",
            "",
            "## 当日文件",
            "",
            *links,
            "",
            "## 今日速记",
            "",
            "> （在此手动补充今天最值得记住的 1~3 件事）",
            "",
        ]
        meta = {"date": date_key, "type": "当日索引", "auto": "true"}
        _date = extract_date(date_key)
        if _date is None:
            continue
        files[date_rel_path(*_date, "当日索引")] = (
            render_frontmatter(meta) + "\n".join(body)
        )


def _export_index(
    files: dict[str, str],
    stats: dict[str, Any],
    disk_files: list[str] | None = None,
) -> None:
    """生成全局导航（README + 主题总览）。

    Args:
        files: 本次将要写入的文件（用于统计）。
        stats: 统计信息。
        disk_files: 磁盘上已有的完整文件列表；提供时主题总览以此为准，
            保证「每日增量导出」也能列出全部历史主题，而不是只列当天主题。
    """
    topic_source = disk_files if disk_files is not None else files
    topic_dirs = sorted({rel.split("/")[1] for rel in topic_source if rel.startswith("主题/") and "/" in rel})
    topic_lines = []
    for t in topic_dirs:
        has_moc = any(rel.startswith(f"主题/{t}/") and rel.endswith("/MOC.md") for rel in topic_source)
        topic_lines.append(f"- **{t}**" + (" · [MOC](./主题/%s/MOC.md)" % t if has_moc else ""))

    readme = [
        "# 思忆集 · 知识库",
        "",
        f"> 自动生成于 {_fmt_now()} · 内容层根目录：``{stats.get('root', '')}``",
        "> 本目录是思忆集个人知识体系的「内容层」（MD 事实源）；知识图谱、全文检索等为派生视图，可随时由本目录重建。",
        "",
        "## 统计",
        "",
        f"- 时间轴文件（资讯摘要/资讯条目/对话/记忆/卡片/当日索引）：{stats.get('digest_files', 0) + stats.get('info_item_days', 0) + stats.get('memory_files', 0) + stats.get('conversation_files', 0) + stats.get('daily_indexes', 0)} 份",
        f"- 每日资讯：{stats.get('info_items_total', 0)} 条原始条目 → {stats.get('info_item_days', 0)} 个资讯条目文件（每日高价值 Top 精选）",
        f"- 原子知识卡片：{stats.get('cards', 0)} 张",
        f"- 主题 MOC：{stats.get('mocs', 0)} 个",
        f"- 本项目对话导出：{stats.get('conversations', 0)} 个会话",
        f"- 图谱快照：{stats.get('graph_entities', 0)} 实体 / {stats.get('graph_relations', 0)} 关系",
        "",
        "## 目录结构",
        "",
        "```",
        "knowledge/",
        "├── 00_索引/              # 全局导航（README、主题总览）",
        "├── YYYY/YYYY-MM/        # 时间轴：当日文件（资讯摘要/对话/记忆/当日索引）",
        "├── 主题/<主题>/          # 主题轴：MOC + 原子卡片",
        "└── 图谱导出/             # 图谱快照",
        "```",
        "",
        "## 使用说明",
        "",
        "- **按时间浏览**：打开 `YYYY/YYYY-MM/` 下的当日文件，从当日索引开始；",
        "- **按主题浏览**：打开 `主题/` 下各主题的 `MOC.md`；",
        "- **改写**：直接编辑任意 MD 文件（保留 `---` frontmatter 头部），保存后系统会回灌到图谱/记忆；",
        "- **重建**：图谱与索引为派生视图，可在「知识图谱」模块触发重建。",
        "",
    ]
    files["00_索引/README.md"] = "\n".join(readme)

    overview = [
        "# 主题总览",
        "",
        "> 由知识卡片归纳生成的主题导航。",
        "",
        *topic_lines,
        "",
    ]
    files["00_索引/主题总览.md"] = "\n".join(overview)


# ── 对外入口 ─────────────────────────────────────────────────────────

def export_all(
    context: Any,
    *,
    max_conversations: int = 0,
    max_graph_entities: int = 400,
) -> dict[str, Any]:
    """把当前沉淀数据全量导出为知识库 MD 文件（幂等重建）。

    Args:
        context: 至少需带 ``local_data_store``；可选 ``chat_session_repository`` /
            ``knowledge_graph_store``。
        max_conversations: 0=全部，否则最多导出 N 个会话。
        max_graph_entities: 图谱快照最多列出的实体数。

    Returns:
        {success, files_written, root, stats…}
    """
    store = getattr(context, "local_data_store", None)
    if store is None:
        return {"success": False, "error": "local_data_store not configured"}

    md = MDKnowledgeStore(store.base_dir)
    files: dict[str, str] = {}
    stats: dict[str, Any] = {
        "digests": 0, "digest_files": 0, "memories": 0, "memory_files": 0,
        "cards": 0, "mocs": 0, "conversations": 0, "conversation_files": 0,
        "graph_entities": 0, "graph_relations": 0, "daily_indexes": 0,
        "info_item_days": 0, "info_items_total": 0,
    }

    _export_memory(context, files, stats)
    _export_info_items(context, files, stats)
    _export_conversations(context, files, stats, max_conversations)
    _export_graph(context, files, stats, max_graph_entities)
    _build_daily_index(files)
    stats["daily_indexes"] = sum(1 for rel in files if rel.endswith("_当日索引.md"))
    stats["root"] = md.root
    _export_index(files, stats)

    written = 0
    for rel, content in files.items():
        md.write(rel, content)
        written += 1
    stats["files_written"] = written
    stats["success"] = True
    logger.info("知识库 MD 导出完成：%d 个文件 → %s", written, md.root)
    return stats


# ── 每日增量导出 ─────────────────────────────────────────────────────

def export_daily(
    context: Any,
    *,
    days: int = 1,
    max_conversations: int = 60,
) -> dict[str, Any]:
    """每日增量导出：把最近 ``days`` 天的资讯+对话+记忆写入当日 MD，并生成当日索引。

    对应阶段二定时任务 ``knowledge.md_daily``。与 ``export_all`` 的关键差异：

    - **不覆盖用户改写的历史文件**：只写最近 ``days`` 天的时间轴文件；
      更早的 MD（用户手动编辑过）保持原样，避免每日任务把回灌结果冲掉。
    - **主题轴只增不改**（F1 修复，2026-09-16）：新卡片/MOC 首次出现即落盘；
      磁盘上已存在同名主题文件时跳过写入，用户编辑区不被每日任务冲掉。
      此前主题轴被整体禁用（include_cards=False），致知识卡片永不出 kv_store。
    - **不重写图谱快照**：图谱为派生视图，由全量导出 / 图谱模块重建。
    - **刷新全局索引**：README / 主题总览按磁盘现有文件轻量刷新。

    Returns:
        {success, files_written, root, days, stats…}
    """
    store = getattr(context, "local_data_store", None)
    if store is None:
        return {"success": False, "error": "local_data_store not configured"}

    md = MDKnowledgeStore(store.base_dir)
    days = max(1, int(days))
    min_date = (datetime.now() - timedelta(days=days - 1)).timetuple()[:3]

    files: dict[str, str] = {}
    stats: dict[str, Any] = {
        "digests": 0, "digest_files": 0, "memories": 0, "memory_files": 0,
        "cards": 0, "mocs": 0, "conversations": 0, "conversation_files": 0,
        "graph_entities": 0, "graph_relations": 0, "daily_indexes": 0,
        "info_item_days": 0, "info_items_total": 0,
    }

    _export_memory(context, files, stats, min_date=min_date, include_cards=True)
    # F1 修复（2026-09-16）：卡片/MOC 此前被 include_cards=False 整体跳过，
    # 91 张卡片只活在 kv_store，主题轴永不落盘（"卡片孤儿"）；而全禁的动机
    # 是怕每日任务冲掉用户对主题文件的编辑。两全方案：照常生成，但主题轴
    # **只补磁盘上不存在的新文件**，已存在的（可能被用户改过）一律不覆盖。
    _preexisting_topic = {r for r in md.list_files() if r.startswith("主题/")}
    if _preexisting_topic:
        skipped_cards = skipped_mocs = 0
        for rel in [r for r in files if r.startswith("主题/") and r in _preexisting_topic]:
            files.pop(rel)
            if rel.endswith("/MOC.md"):
                skipped_mocs += 1
            else:
                skipped_cards += 1
        stats["cards_skipped_exists"] = skipped_cards
        stats["mocs_skipped_exists"] = skipped_mocs
    # 卡片/MOC 计数以实际将要写入的为准（_export_memory 按生成计数）
    stats["cards"] = sum(1 for r in files if r.startswith("主题/") and "/卡片-" in r)
    stats["mocs"] = sum(1 for r in files if r.startswith("主题/") and r.endswith("/MOC.md"))
    _export_info_items(context, files, stats, min_date=min_date)
    _export_conversations(context, files, stats, max_conversations, min_date=min_date)
    _build_daily_index(files)
    stats["daily_indexes"] = sum(1 for rel in files if rel.endswith("_当日索引.md"))
    stats["root"] = md.root
    _export_index(files, stats, disk_files=md.list_files())

    written = 0
    for rel, content in files.items():
        md.write(rel, content)
        written += 1
    stats["files_written"] = written
    stats["success"] = True
    stats["incremental"] = True
    stats["days"] = days
    logger.info("知识库 MD 每日导出完成：%d 个文件（最近 %d 天）→ %s", written, days, md.root)
    return stats


__all__ = ["export_all", "export_daily"]
