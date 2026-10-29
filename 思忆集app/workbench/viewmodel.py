"""WorkbenchViewModel — 工作台卡片与建议列表的状态管理。

根据 main-window-decoupling-plan.md 设计：
- 纯 Python，不持有 Qt Widget 引用
- 负责从 Service 层数据转换为视图模型
- 可纯逻辑单测
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

_MAX_SUGGESTIONS = 6
_NEWS_DIGEST_MAX_LEN = 72

_NEWS_FILTER_LABELS: dict[str, str] = {
    "all": "全部",
    "world": "世界",
    "policy": "政策",
    "ai": "AI",
    "internet": "互联网",
    "today_policy": "今日政策",
    "today_ai": "今日AI",
}


class WorkbenchViewModel:
    """工作台视图模型：管理卡片列表、可见性、排序、建议。"""

    @staticmethod
    def build_suggestions(snapshot: dict[str, Any],
                          extra_suggestions: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
        """从 dashboard snapshot 构建建议列表。

        返回: [{"text": "...", "action": "..."}, ...]
        extra_suggestions 将前置插入（如 doctor 健康提示）。
        """
        suggestions: list[dict[str, str]] = list(extra_suggestions or [])
        pending = snapshot.get("pending_tasks", [])
        notes = snapshot.get("notes", [])
        proposals = snapshot.get("improvement_proposals", [])

        # 待审提案置顶
        pending_proposals = [p for p in proposals if p.get("status") == "pending_review"]
        for p in pending_proposals:
            title = p.get("title") or p.get("summary", "")
            if title:
                suggestions.append({"text": f"审阅提案：{title}", "action": "proposals"})

        # 待办任务建议
        if pending:
            suggestions.append({
                "text": f"先处理待办，当前有 {len(pending)} 个未完成任务",
                "action": "tasks",
            })

        # 笔记建议
        if notes:
            suggestions.append({
                "text": "打开笔记，整理最近一条记录的标题、分类和标签",
                "action": "notes",
            })
        else:
            suggestions.append({
                "text": "先写一条今日笔记，把任务和想法沉淀下来",
                "action": "notes",
            })

        # 收件建议
        suggestions.append({
            "text": "先把当前想到的内容收进收件箱",
            "action": "capture",
        })

        return suggestions[:_MAX_SUGGESTIONS]

    @staticmethod
    def build_suggestions_meta_text(suggestions: list[dict[str, str]],
                                     proposals: list[dict[str, Any]]) -> str:
        """构建建议区元信息文案。"""
        pending_proposals = [p for p in proposals if p.get("status") == "pending_review"]
        parts = [f"当前建议 {len(suggestions)} 条"]
        if pending_proposals:
            parts.append(f"待审提案 {len(pending_proposals)} 条")
        return " · ".join(parts)

    @staticmethod
    def build_recent_lists(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """构建各模块最近列表（按 updated_at 倒序，截取前 N 条）。

        返回: {"tasks": [...], "notes": [...], "news": [...], "translations": [...], "inbox": [...]}
        """
        return {
            "tasks": WorkbenchViewModel._sorted_items(snapshot.get("tasks", []), 3),
            "notes": WorkbenchViewModel._sorted_items(snapshot.get("notes", []), 3),
            "news": WorkbenchViewModel._sorted_items(
                snapshot.get("news_cache_items") or snapshot.get("news_records", []), 5
            ),
            "translations": WorkbenchViewModel._sorted_items(
                snapshot.get("translations", []), 3
            ),
            "inbox": WorkbenchViewModel._sorted_items(
                snapshot.get("inbox_entries", []), 3
            ),
        }

    @staticmethod
    def build_card_summary(snapshot: dict[str, Any]) -> dict[str, int]:
        """构建卡片摘要计数。"""
        return {
            "tasks_pending": len(snapshot.get("pending_tasks", [])),
            "notes_total": len(snapshot.get("notes", [])),
            "news_favorites": len(snapshot.get("favorite_news_keys", [])),
            "translations_total": len(snapshot.get("translations", [])),
            "alarms_total": len(snapshot.get("alarms", [])),
        }

    @staticmethod
    def _sorted_items(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        """按 updated_at 倒序排序并截取前 limit 条。"""
        sorted_items = sorted(
            items,
            key=lambda x: x.get("updated_at", x.get("created_at", "")),
            reverse=True,
        )
        return sorted_items[:limit]

    @staticmethod
    def extract_item_title(item: dict[str, Any], fallback: str = "未命名") -> str:
        """从条目中安全提取标题。"""
        return str(
            item.get("title")
            or item.get("name")
            or item.get("source_text")
            or item.get("news_title")
            or fallback
        )

    __DIGEST_FIELDS = ("summary", "description", "content", "note", "source", "source_name")

    @staticmethod
    def _format_timestamp(value: str | None) -> str:
        """格式化时间戳为 MM-DD HH:MM 或截断字符串。"""
        text = str(value or "").strip()
        if not text:
            return ""
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed.strftime("%m-%d %H:%M")
        except ValueError:
            return text[:16]

    @staticmethod
    def _extract_digest(item: dict[str, Any], title: str) -> str:
        """从新闻条目中提取摘要。"""
        for field_name in WorkbenchViewModel.__DIGEST_FIELDS:
            value = str(item.get(field_name) or "").strip().replace("\n", " ")
            if value and value != title:
                return (value[:_NEWS_DIGEST_MAX_LEN] + "...") if len(value) > _NEWS_DIGEST_MAX_LEN else value
        return ""

    @staticmethod
    def build_news_preview(all_news: list[dict[str, Any]],
                           filter_label: str = "全部") -> dict[str, Any]:
        """构建新闻预览数据模型。

        参数:
            all_news: 已排序的新闻列表（可按 updated_at 降序传入）
            filter_label: 当前筛选标签的中文名

        返回:
            dict 包含:
              - empty: bool
              - lead_title: str
              - lead_meta: str (来源 · 时间 · 摘要)
              - digest_label: str (筛选信息)
              - items: list[{"text": str, "data": dict}]
        """
        if not all_news:
            return {
                "empty": True,
                "lead_title": "今日还没有新闻推送",
                "lead_meta": '当前筛选条件下没有匹配新闻，试试切回"全部"或刷新新闻。',
                "digest_label": "",
                "items": [],
            }

        lead = all_news[0]
        title = str(lead.get("title") or lead.get("news_title") or "今日重点新闻").strip() or "今日重点新闻"
        digest = WorkbenchViewModel._extract_digest(lead, title)

        source = str(lead.get("source") or lead.get("source_name") or "").strip()
        updated = WorkbenchViewModel._format_timestamp(lead.get("updated_at") or lead.get("created_at"))
        meta_parts = [p for p in (source, updated) if p]
        meta_text = " · ".join(meta_parts) if meta_parts else "今日最新一条"
        if digest:
            meta_text = f"{meta_text} · {digest}"

        items = []
        for item in all_news[:5]:
            item_title = str(item.get("title") or "未命名新闻")
            item_source = str(item.get("source") or "未知来源")
            items.append({"text": f"{item_title} · {item_source}", "data": item})

        return {
            "empty": False,
            "lead_title": title,
            "lead_meta": meta_text,
            "digest_label": "",
            "items": items,
        }

    @staticmethod
    def build_news_filter_label(filter_key: str) -> str:
        """返回筛选键对应的中文标签。"""
        return _NEWS_FILTER_LABELS.get(filter_key, "全部")
