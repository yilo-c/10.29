"""用量统计 — 记录用户操作频次，供能力注册表匹配使用。

数据存储在 data/usage_stats.json，结构：
{
  "bookmark_domains": { "github.com": 7, "arxiv.org": 3, ... },
  "actions": {
    "bookmark": { "2026-06-01": 5, "2026-06-02": 3, ... },
    "create_note": { "2026-06-01": 2, ... },
    "translate": { ... },
    "view_weather": { ... },
    "create_todo": { ... },
  },
  "last_suggestion": { "github_repo": "2026-06-01", ... },
}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any

logger = logging.getLogger("siyi.services.usage_stats")


def _get_stats_path(context) -> str:
    data_store = getattr(context, "local_data_store", None)
    if data_store and hasattr(data_store, "base_dir"):
        return os.path.join(data_store.base_dir, "usage_stats.json")
    fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    return os.path.join(os.path.abspath(fallback), "usage_stats.json")


def _load_stats(context) -> dict[str, Any]:
    try:
        path = _get_stats_path(context)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.debug("Failed to load usage stats: %s", exc)
    return {"bookmark_domains": {}, "actions": {}, "last_suggestion": {}}


def _save_stats(context, data: dict[str, Any]) -> None:
    try:
        path = _get_stats_path(context)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 原子写：先写临时文件再 os.replace，防止崩溃损坏用量统计
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception as exc:
        logger.debug("Failed to save usage stats: %s", exc)


def record_action(context, action: str, domain: str | None = None) -> None:
    """记录一次用户操作。

    Args:
        action: 操作类型（bookmark / create_note / translate / view_weather / create_todo）
        domain: 关联域名（bookmark 时传入，如 github.com）
    """
    stats = _load_stats(context)
    today = date.today().isoformat()

    # 按域名统计收藏
    if action == "bookmark" and domain:
        domains = stats.setdefault("bookmark_domains", {})
        domains[domain] = domains.get(domain, 0) + 1

    # 按时序统计操作
    actions = stats.setdefault("actions", {})
    action_counts = actions.setdefault(action, {})
    action_counts[today] = action_counts.get(today, 0) + 1

    _save_stats(context, stats)

    # V2-02（2026-08-24）：usage.record（前端行为上报）同步落行为日志，补前端采集盲区。
    # C5 约束：BehaviorEvent 默认 privacy=local，仅本地存储、不上云。
    # record_action 仅由 usage.record RPC 调用，故此处每条事件均来自前端上报。
    behavior = getattr(context, "behavior_logger", None)
    if behavior is not None and hasattr(behavior, "record"):
        try:
            from domain.models.behavior import BehaviorEvent

            behavior.record(
                BehaviorEvent(
                    category="app",
                    action=action,
                    source="frontend",
                    detail={"domain": domain} if domain else {},
                )
            )
        except Exception:
            logger.debug("failed to record usage behavior event", exc_info=True)


def get_stats(context) -> dict[str, Any]:
    """获取用量统计，附带汇总信息。"""
    stats = _load_stats(context)
    actions = stats.get("actions", {})

    # 计算总量
    total_counts: dict[str, int] = {}
    for action, days in actions.items():
        total_counts[action] = sum(days.values()) if isinstance(days, dict) else 0

    stats["totals"] = total_counts
    return stats


def get_suggestion_due(context, cap_id: str, cooldown_days: int = 14) -> bool:
    """检查某个能力的建议是否已冷却到期（避免重复推送）。"""
    stats = _load_stats(context)
    last = stats.get("last_suggestion", {}).get(cap_id)
    if not last:
        return True
    try:
        last_date = datetime.strptime(last, "%Y-%m-%d").date()
        return (date.today() - last_date).days >= cooldown_days
    except ValueError:
        return True


def mark_suggestion_sent(context, cap_id: str) -> None:
    """标记某个能力的建议已推送。"""
    stats = _load_stats(context)
    stats.setdefault("last_suggestion", {})[cap_id] = date.today().isoformat()
    _save_stats(context, stats)


def mark_enabled(context, cap_id: str) -> None:
    """用户批准了某个能力，记录一下。"""
    from .capability_registry import set_enabled

    set_enabled(cap_id, True)
    stats = _load_stats(context)
    stats.setdefault("approved", {})[cap_id] = date.today().isoformat()
    _save_stats(context, stats)
