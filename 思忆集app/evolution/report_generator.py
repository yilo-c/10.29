"""早报技术板块生成器：雷达结果 → 每日晨报「技术动态」板块。

根据 complete-architecture-design.md 第 6.1 节设计：
- TechRadar 扫描结果 → 格式化为每日早报的「技术动态」板块
- 按变更等级分组（Level 0/1/2）
- 用户可直接点击操作（评估/忽略/自动升级/查看详情）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .tech_radar import LEVEL_CONFIG, LEVEL_FEATURE, LEVEL_KNOWLEDGE, RadarFinding

logger = logging.getLogger("siyi.evolution.report")

_LEVEL_LABELS = {
    LEVEL_KNOWLEDGE: "馃摎 知识更新",
    LEVEL_CONFIG: "馃敡 可用升级",
    LEVEL_FEATURE: "⚠️ 功能/代码变更",
}

_LEVEL_ACTIONS = {
    LEVEL_KNOWLEDGE: ["确认更新", "忽略"],
    LEVEL_CONFIG: ["评估集成", "查看详情", "暂缓"],
    LEVEL_FEATURE: ["查看详情", "暂缓"],
}


@dataclass
class TechBriefingSection:
    """早报中技术动态板块的内容。"""
    items: list[dict[str, Any]] = field(default_factory=list)
    weekly_summary: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": list(self.items),
            "weekly_summary": self.weekly_summary,
            "generated_at": self.generated_at or datetime.now(timezone.utc).isoformat(),
        }


class EvolutionReportGenerator:
    """早报技术板块生成器。

    职责：
    1. 接收 TechRadar 扫描发现
    2. 按变更等级格式化输出
    3. 生成可操作的按钮/链接
    """

    @staticmethod
    def generate(findings: list[RadarFinding], weekly_summary: str = "") -> TechBriefingSection:
        """将雷达发现格式化为早报板块。"""
        if not findings:
            return TechBriefingSection(
                items=[],
                weekly_summary=weekly_summary or "本周暂无技术动态更新。",
                generated_at=datetime.now(timezone.utc).isoformat(),
            )

        grouped: dict[int, list[dict[str, Any]]] = {}
        for f in findings:
            level = f.level
            if level not in grouped:
                grouped[level] = []
            grouped[level].append({
                "name": f.name,
                "description": f.description,
                "current_version": f.current_version,
                "latest_version": f.latest_version,
                "url": f.url,
                "has_breaking_changes": f.has_breaking_changes,
                "category": f.category,
                "actions": _LEVEL_ACTIONS.get(level, ["查看详情"]),
            })

        # 按等级排序输出
        items: list[dict[str, Any]] = []
        for level in sorted(grouped.keys(), reverse=True):
            label = _LEVEL_LABELS.get(level, f"Level {level}")
            items.append({"section_title": label, "items": grouped[level]})

        return TechBriefingSection(
            items=items,
            weekly_summary=weekly_summary or f"共发现 {len(findings)} 项技术动态",
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def format_as_text(section: TechBriefingSection) -> str:
        """将技术板块格式化为纯文本（用于早报展示）。"""
        lines = ["馃敩 技术雷达（本周变化）", ""]

        if not section.items:
            lines.append("  本周暂无技术动态更新。")
            return "\n".join(lines)

        for group in section.items:
            title = group.get("section_title", "")
            items = group.get("items", [])
            lines.append(f"  {title}:")
            for item in items:
                breaking = " ⚠️ Breaking" if item.get("has_breaking_changes") else ""
                lines.append(f"  ├─ {item['name']}{breaking}")
                lines.append(f"  │  {item['description']}")
                actions = " | ".join(item.get("actions", []))
                lines.append(f"  │  [{actions}]")
            lines.append("")

        if section.weekly_summary:
            lines.append(f"  馃挕 {section.weekly_summary}")

        return "\n".join(lines)
