"""能力注册表 — 定义思忆集可主动拓展的外部能力。

每条能力定义：
- 触发条件（用户行为达到什么频次）
- 接入后能做什么
- 风险等级（低/中/高）
- 是否已有配置

不依赖 AI，纯规则匹配。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Capability:
    """一项可拓展的外部能力。"""
    id: str
    label: str                         # 用户看到的名称
    description: str                   # 做什么用
    match_pattern: dict[str, Any]      # 匹配规则
    api_endpoint: str                  # 接入的 API
    risk: str                          # 'low' | 'medium' | 'high'
    enabled: bool = False              # 用户是否已启用
    icon: str = "馃攲"


# ── 静态能力注册表 ──────────────────────────────────────────

REGISTRY: list[Capability] = [
    Capability(
        id="github_repo",
        label="GitHub 仓库信息",
        description="收藏 GitHub 链接时自动显示 Star 数、语言、最近更新",
        match_pattern={"url_domain": "github.com", "action": "bookmark", "min_count": 3},
        api_endpoint="https://api.github.com",
        risk="low",
        icon="馃悪",
    ),
    Capability(
        id="arxiv_paper",
        label="arXiv 论文元数据",
        description="收藏 arxiv 论文时自动提取摘要、作者、分类",
        match_pattern={"url_domain": "arxiv.org", "action": "bookmark", "min_count": 3},
        api_endpoint="http://export.arxiv.org",
        risk="low",
        icon="馃搫",
    ),
    Capability(
        id="youtube_transcript",
        label="YouTube 视频摘要",
        description="收藏 YouTube 链接时自动提取视频描述和字幕摘要",
        match_pattern={"url_domain": "youtube.com", "action": "bookmark", "min_count": 3},
        api_endpoint="https://www.youtube.com",
        risk="low",
        icon="馃幀",
    ),
    Capability(
        id="notion_sync",
        label="笔记同步到 Notion",
        description="将思忆集的笔记自动同步到 Notion 数据库",
        match_pattern={"action": "create_note", "min_count": 20, "period_days": 30},
        api_endpoint="https://api.notion.com",
        risk="medium",
        icon="馃摑",
    ),
    Capability(
        id="todoist_sync",
        label="待办同步到 Todoist",
        description="将思忆集的待办自动同步到 Todoist",
        match_pattern={"action": "create_todo", "min_count": 30, "period_days": 30},
        api_endpoint="https://api.todoist.com",
        risk="medium",
        icon="✅",
    ),
    Capability(
        id="wechat_reading",
        label="微信读书同步",
        description="将微信读书的划线/笔记导入思忆集",
        match_pattern={"action": "bookmark", "url_domain": "weread.qq.com", "min_count": 2},
        api_endpoint="N/A（需扫码）",
        risk="medium",
        icon="馃摉",
    ),
    Capability(
        id="weather_auto",
        label="天气自动获取",
        description="每天自动获取天气并推送到桌面通知",
        match_pattern={"action": "view_weather", "min_count": 10, "period_days": 30},
        api_endpoint="https://wttr.in",
        risk="low",
        icon="馃尋",
    ),
]


def get_capability(cap_id: str) -> Capability | None:
    for c in REGISTRY:
        if c.id == cap_id:
            return c
    return None


def get_disabled() -> list[Capability]:
    return [c for c in REGISTRY if not c.enabled]


def get_enabled() -> list[Capability]:
    return [c for c in REGISTRY if c.enabled]


def set_enabled(cap_id: str, enabled: bool) -> bool:
    cap = get_capability(cap_id)
    if cap:
        cap.enabled = enabled
        return True
    return False


def match_capabilities(stats: dict[str, Any]) -> list[tuple[Capability, str]]:
    """根据用量统计，匹配出待建议的能力列表。

    Args:
        stats: usage_stats 输出的统计字典

    Returns:
        [(Capability, 匹配理由), ...]
    """
    results: list[tuple[Capability, str]] = []

    for cap in get_disabled():
        pattern = cap.match_pattern
        action = pattern.get("action", "")
        domain = pattern.get("url_domain", "")
        min_count = pattern.get("min_count", 3)
        period = pattern.get("period_days", 30)

        # 按动作类型匹配
        if action == "bookmark" and domain:
            count = _count_bookmarks_by_domain(stats, domain, period)
            if count >= min_count:
                results.append((cap, f"已收藏 {count} 个 {domain} 链接"))
        elif action == "bookmark" and not domain:
            count = _count_action(stats, "bookmark", period)
            if count >= min_count:
                results.append((cap, f"已收藏 {count} 个链接"))
        elif action == "create_note":
            count = _count_action(stats, "create_note", period)
            if count >= min_count:
                results.append((cap, f"已创建 {count} 条笔记"))
        elif action == "create_todo":
            count = _count_action(stats, "create_todo", period)
            if count >= min_count:
                results.append((cap, f"已创建 {count} 个待办"))
        elif action == "view_weather":
            count = _count_action(stats, "view_weather", period)
            if count >= min_count:
                results.append((cap, f"本月查看了 {count} 次天气"))
        elif action == "translate":
            count = _count_action(stats, "translate", period)
            if count >= min_count:
                results.append((cap, f"已翻译 {count} 次"))

    return results


def _count_action(stats: dict[str, Any], action: str, period_days: int) -> int:
    actions = stats.get("actions", {})
    counts = actions.get(action, {})
    total = sum(counts.values()) if isinstance(counts, dict) else 0
    return total


def _count_bookmarks_by_domain(stats: dict[str, Any], domain: str, period_days: int) -> int:
    domains = stats.get("bookmark_domains", {})
    return domains.get(domain, 0)
