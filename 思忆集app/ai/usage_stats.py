"""AI 用量统计 — 汇总 Token、成本、调用次数等数据供可观测性面板使用。"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("siyi.ai.usage_stats")


class AIUsageStats:
    """从 CostTracker 和 BudgetManager 的持久化数据中聚合用量统计。

    提供按天/周/月汇总的 Token 消耗、成本、调用次数、模型分布，
    数据格式可直接供 QtCharts 图表渲染。
    """

    def __init__(self, data_store=None, cost_tracker=None, budget_manager=None):
        self._data_store = data_store
        self._cost_tracker = cost_tracker
        self._budget_manager = budget_manager

    def get_daily_stats(self, days: int = 30) -> dict[str, Any]:
        """获取每日用量汇总。"""
        records = self._load_records()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        daily: dict[str, dict[str, Any]] = {}
        for rec in records:
            ts = rec.get("timestamp", "")
            if ts < cutoff.isoformat():
                continue
            day = ts[:10]
            if day not in daily:
                daily[day] = {"tokens": 0, "cost": 0.0, "calls": 0, "models": set()}
            daily[day]["tokens"] += rec.get("tokens", 0)
            daily[day]["cost"] += rec.get("cost", 0.0)
            daily[day]["calls"] += 1
            model = rec.get("model", "unknown")
            if model:
                daily[day]["models"].add(model)
        # Convert sets to lists for JSON serialization
        for d in daily.values():
            d["models"] = sorted(d["models"])
        return dict(sorted(daily.items()))

    def get_weekly_stats(self, weeks: int = 12) -> dict[str, Any]:
        """获取每周用量汇总。"""
        records = self._load_records()
        cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)
        weekly: dict[str, dict[str, Any]] = {}
        for rec in records:
            ts = rec.get("timestamp", "")
            if ts < cutoff.isoformat():
                continue
            try:
                dt = datetime.fromisoformat(ts)
                week_key = dt.strftime("%Y-W%W")
            except (ValueError, TypeError):
                week_key = ts[:7]
            if week_key not in weekly:
                weekly[week_key] = {"tokens": 0, "cost": 0.0, "calls": 0, "models": set()}
            weekly[week_key]["tokens"] += rec.get("tokens", 0)
            weekly[week_key]["cost"] += rec.get("cost", 0.0)
            weekly[week_key]["calls"] += 1
            model = rec.get("model", "unknown")
            if model:
                weekly[week_key]["models"].add(model)
        for w in weekly.values():
            w["models"] = sorted(w["models"])
        return dict(sorted(weekly.items()))

    def get_monthly_stats(self, months: int = 12) -> dict[str, Any]:
        """获取每月用量汇总。"""
        records = self._load_records()
        # P3-5: months * 31 会让 2 月等短月被多包含一天的窗口；改为精确按年月回退
        now = datetime.now(timezone.utc)
        # 计算 N 个月前的精确边界：年月相减再补 1 号 0 点
        total_months = now.year * 12 + (now.month - 1) - months
        cutoff_year, cutoff_month = divmod(total_months, 12)
        cutoff = datetime(cutoff_year, cutoff_month + 1, 1, tzinfo=timezone.utc)
        monthly: dict[str, dict[str, Any]] = {}
        for rec in records:
            ts = rec.get("timestamp", "")
            if ts < cutoff.isoformat():
                continue
            month_key = ts[:7]
            if month_key not in monthly:
                monthly[month_key] = {"tokens": 0, "cost": 0.0, "calls": 0, "models": set()}
            monthly[month_key]["tokens"] += rec.get("tokens", 0)
            monthly[month_key]["cost"] += rec.get("cost", 0.0)
            monthly[month_key]["calls"] += 1
            model = rec.get("model", "unknown")
            if model:
                monthly[month_key]["models"].add(model)
        for m in monthly.values():
            m["models"] = sorted(m["models"])
        return dict(sorted(monthly.items()))

    def get_model_distribution(self, days: int = 30) -> dict[str, int]:
        """获取模型调用次数分布。"""
        records = self._load_records()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        dist: dict[str, int] = defaultdict(int)
        for rec in records:
            ts = rec.get("timestamp", "")
            if ts < cutoff.isoformat():
                continue
            model = rec.get("model", "unknown")
            dist[model] += 1
        return dict(sorted(dist.items(), key=lambda x: -x[1]))

    def get_tier_distribution(self, days: int = 30) -> dict[str, int]:
        """获取层级（local/judge/mid_cloud/high_cloud）调用分布。"""
        records = self._load_records()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        dist: dict[str, int] = defaultdict(int)
        for rec in records:
            ts = rec.get("timestamp", "")
            if ts < cutoff.isoformat():
                continue
            tier = rec.get("tier", "unknown")
            dist[tier] += 1
        return dict(sorted(dist.items(), key=lambda x: -x[1]))

    def _load_records(self) -> list[dict[str, Any]]:
        if self._cost_tracker is not None and hasattr(self._cost_tracker, "get_all_records"):
            return self._cost_tracker.get_all_records()
        if self._data_store is not None:
            return self._data_store.load_json("cost_tracker_records", [])
        return []
