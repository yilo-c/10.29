"""知识图谱多域采集层 —— 把「散在各业务域的原文」归一成同一种可提取条目。

P4-KG-CAPTURE（2026-09-26）。补的缺口：图谱历史上只接了 3 类来源
（DeepSeek 对话导入、结构化记忆回灌、MD 回灌），**笔记 / 收件箱 / 任务 / 词库
四个域根本没有进图谱的通道** —— 所以真实图谱 739 个节点里 79% 是孤立点、
65% 说不清自己从哪来，用户点开节点自然"没有完整信息可显示"。

这一层做的就是用户侧期待的那次「统一转化」：各仓储 → 同一形状
`{kind, id, title, content, created_at}`，再由 `KnowledgeGraphExtractor`
以 LLM 通道提取，出处统一写成 `{kind}:{id}`（P4-KG-ORIGIN）。

与 `backend/handlers/_timeline.py` 的 `_build_entry(source_type=…)` 同范式。

调用入口只有一个：`knowledge.backfill` RPC —— 严格 **opt-in**（不挂 cron、
不自动跑，逐批推进 offset），因为这批量每跑一条都是一次真实 LLM 调用。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("siyi.knowledge.domain_capture")

# 采集域词表（同时是 `knowledge.backfill` 的 `domains` 合法取值）
CAPTURABLE_DOMAINS: tuple[str, ...] = ("memory", "note", "inbox", "task", "vocab")


def _clean_text(value: Any, *, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _memory_items(context: Any) -> list[dict[str, Any]]:
    store = getattr(context, "local_data_store", None)
    if store is None:
        return []
    try:
        raw = store.load_json("structured_memory_entries", []) or []
    except Exception as exc:  # noqa: BLE001 — 读不到只是少一域，不炸整轮
        logger.warning("capture: structured_memory_entries 读取失败: %s", exc)
        return []
    rows: list[dict[str, Any]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping):
            continue
        content = _clean_text(entry.get("content") or entry.get("summary"))
        if not content:
            continue
        rows.append(
            {
                "kind": "memory",
                "id": str(entry.get("id") or ""),
                "title": _clean_text(entry.get("title"), limit=120),
                "content": content,
                "created_at": str(entry.get("created_at") or ""),
            }
        )
    return rows


def _repo_items(context: Any, attr: str, method: str, *, kind: str, title_keys: tuple[str, ...], body_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """通用仓储采集：取 `context.<attr>.<method>()` 并按键名挑标题/正文。

    仓储缺失或方法签名不兼容（旧 mock）时返回空列表并记一条 warning——
    一域失败不能带走整轮回填。
    """
    repo = getattr(context, attr, None)
    loader: Callable[..., Any] | None = getattr(repo, method, None)
    if not callable(loader):
        return []
    try:
        rows_raw = loader()
    except Exception as exc:  # noqa: BLE001
        logger.warning("capture: %s.%s 调用失败（跳过该域）: %s", attr, method, exc)
        return []
    rows: list[dict[str, Any]] = []
    for item in rows_raw if isinstance(rows_raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        title = ""
        for key in title_keys:
            title = _clean_text(item.get(key), limit=120)
            if title:
                break
        body = ""
        for key in body_keys:
            body = _clean_text(item.get(key))
            if body:
                break
        if not (title or body):
            continue
        rows.append(
            {
                "kind": kind,
                "id": str(item.get("id") or item.get("note_id") or item.get("task_id") or ""),
                "title": title,
                "content": body or title,
                "created_at": str(item.get("created_at") or item.get("create_time") or ""),
            }
        )
    return rows


def _note_items(context: Any) -> list[dict[str, Any]]:
    return _repo_items(
        context,
        "personal_note_repository",
        "list_notes",
        kind="note",
        title_keys=("title", "name"),
        body_keys=("content", "body", "text"),
    )


def _task_items(context: Any) -> list[dict[str, Any]]:
    return _repo_items(
        context,
        "personal_task_repository",
        "list_tasks",
        kind="task",
        title_keys=("title", "name"),
        body_keys=("description", "content", "note"),
    )


def _vocab_items(context: Any) -> list[dict[str, Any]]:
    return _repo_items(
        context,
        "vocabulary_repository",
        "list_words",
        kind="vocab",
        title_keys=("word", "name"),
        body_keys=("meaning", "definition", "example"),
    )


def _inbox_items(context: Any) -> list[dict[str, Any]]:
    repo = getattr(context, "personal_inbox_repository", None)
    loader = getattr(repo, "list_entries", None)
    if not callable(loader):
        return []
    try:
        rows_raw = loader(include_archived=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("capture: inbox.list_entries 调用失败（跳过该域）: %s", exc)
        return []
    rows: list[dict[str, Any]] = []
    for item in rows_raw if isinstance(rows_raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        title = _clean_text(item.get("title"), limit=120)
        body = _clean_text(item.get("content") or item.get("text") or title)
        if not body:
            continue
        rows.append(
            {
                "kind": "inbox",
                "id": str(item.get("id") or ""),
                "title": title,
                "content": body,
                "created_at": str(item.get("created_at") or ""),
            }
        )
    return rows


_DOMAIN_LOADERS: dict[str, Callable[[Any], list[dict[str, Any]]]] = {
    "memory": _memory_items,
    "note": _note_items,
    "inbox": _inbox_items,
    "task": _task_items,
    "vocab": _vocab_items,
}


def normalize_domains(raw: Any) -> list[str]:
    """把 `domains` 入参（字符串/列表/空）归一成合法域列表，保序去重。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ["memory"]
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    elif isinstance(raw, (list, tuple, set)):
        parts = [str(p).strip() for p in raw]
    else:
        parts = [str(raw).strip()]
    out: list[str] = []
    for part in parts:
        if part in ("all", "*"):
            for domain in CAPTURABLE_DOMAINS:
                if domain not in out:
                    out.append(domain)
            continue
        if part in _DOMAIN_LOADERS and part not in out:
            out.append(part)
    return out or ["memory"]


def _within_days(item: Mapping[str, Any], days: int) -> bool:
    if days <= 0:
        return True
    created = str(item.get("created_at") or "")[:10]
    if not created:
        return False  # 没有时间戳的条目不满足"最近 N 天"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return created >= cutoff


def collect_capture_items(context: Any, domains: Any = None, *, days: int = 0) -> list[dict[str, Any]]:
    """按域采集待提取条目，按 `created_at` 倒序（新优先）合并。

    Returns:
        `[{kind, id, title, content, created_at}, ...]`；某域仓储缺失/报错只是
        少一域，不影响其他域。
    """
    picked = normalize_domains(domains)
    collected: list[dict[str, Any]] = []
    for domain in picked:
        loader = _DOMAIN_LOADERS.get(domain)
        if loader is None:
            continue
        try:
            rows = loader(context)
        except Exception as exc:  # noqa: BLE001 — 采集层永不抛给 RPC
            logger.warning("capture: 域 %s 采集异常（跳过）: %s", domain, exc)
            continue
        collected.extend(row for row in rows if _within_days(row, int(days or 0)))
    collected.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return collected


def capture_text(item: Mapping[str, Any]) -> str:
    """把一条采集条目拼成送抽取的文本（标题与正文分开，避免类型误判）。"""
    title = str(item.get("title") or "").strip()
    content = str(item.get("content") or "").strip()
    if title and content and title not in content:
        return f"{title} | {content}"
    return content or title
