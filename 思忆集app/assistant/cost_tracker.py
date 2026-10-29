"""成本追踪器：按层级追踪 AI 模型调用成本。

根据 dual-positioning-architecture-plan.md 第 2.2 节设计：
- 本地 AI (Ollama)：$0
- Judge (DeepSeek Flash)：~$0.001/次
- 便宜云端 (DeepSeek)：~$0.003/次
- 强大云端 (Claude/GPT)：~$0.03/次

支持日/月汇总、预算预警、历史查询。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("siyi.assistant.cost_tracker")

# 成本层级定义
TIER_LOCAL = "local"         # 本地 AI，$0
TIER_JUDGE = "judge"         # Judge 调度，~$0.001/次
TIER_MID_CLOUD = "mid_cloud"  # 便宜云端，~$0.003/次
TIER_HIGH_CLOUD = "high_cloud"  # 强大云端，~$0.03/次

TIER_NAMES = {
    TIER_LOCAL: "本地 AI (Ollama)",
    TIER_JUDGE: "Judge (DeepSeek Flash)",
    TIER_MID_CLOUD: "便宜云端 (DeepSeek)",
    TIER_HIGH_CLOUD: "强大云端 (Claude/GPT)",
}

# 默认预估成本（美元/次）
_DEFAULT_TIER_COST = {
    TIER_LOCAL: 0.0,
    TIER_JUDGE: 0.001,
    TIER_MID_CLOUD: 0.003,
    TIER_HIGH_CLOUD: 0.03,
}


@dataclass
class CostRecord:
    """单次 AI 调用成本记录。"""
    tier: str                        # 成本层级
    cost_usd: float                  # 本次调用成本
    task_type: str = ""              # 任务类型
    model: str = ""                  # 使用的模型
    timestamp: str = ""              # ISO 时间戳
    tokens: int = 0                  # token 用量
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "cost_usd": self.cost_usd,
            "task_type": self.task_type,
            "model": self.model,
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
            "tokens": self.tokens,
            "metadata": dict(self.metadata),
        }


class CostTracker:
    """AI 成本追踪器。

    职责：
    1. 记录每次 AI 调用的成本，按层级归类
    2. 提供日/月汇总统计
    3. 支持预算上限检查和预警
    4. 持久化到 data_store 供后续查询
    """

    def __init__(self, data_store=None, budget_callback: Callable[[float], bool] | None = None):
        self._data_store = data_store
        self._budget_callback = budget_callback  # 返回 True=继续, False=触发预警
        self._records: list[CostRecord] = []
        self._today_total: float = 0.0
        self._month_total: float = 0.0
        self._tier_totals: dict[str, float] = {t: 0.0 for t in TIER_NAMES}

    def record(
        self,
        *,
        tier: str,
        cost_usd: float | None = None,
        task_type: str = "",
        model: str = "",
        tokens: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """记录一次 AI 调用成本。返回本次记录 ID。"""
        if tier not in TIER_NAMES:
            logger.warning("未知成本层级: %s，按 mid_cloud 处理", tier)
            tier = TIER_MID_CLOUD

        resolved_cost = cost_usd if cost_usd is not None else _DEFAULT_TIER_COST.get(tier, 0.0)

        record = CostRecord(
            tier=tier,
            cost_usd=resolved_cost,
            task_type=task_type,
            model=model,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tokens=tokens,
            metadata=dict(metadata or {}),
        )

        self._records.append(record)
        self._today_total += resolved_cost
        self._month_total += resolved_cost
        self._tier_totals[tier] = self._tier_totals.get(tier, 0.0) + resolved_cost

        # 预算回调检查
        if self._budget_callback is not None:
            try:
                should_continue = self._budget_callback(self._today_total)
                if not should_continue:
                    logger.warning("今日预算已超限: $%.4f", self._today_total)
            except Exception as exc:
                logger.warning("budget callback 异常: %s", exc, exc_info=True)

        # 持久化
        if self._data_store is not None:
            self._persist()

        return record.timestamp

    def get_tier_totals(self) -> dict[str, float]:
        """返回每个层级的累计成本。"""
        return dict(self._tier_totals)

    def get_today_total(self) -> float:
        return self._today_total

    def get_month_total(self) -> float:
        return self._month_total

    def get_daily_summary(self) -> dict[str, Any]:
        """生成今日成本汇总。"""
        tier_breakdown = {
            name: {
                "cost": self._tier_totals.get(tier, 0.0),
                "count": sum(1 for r in self._records if r.tier == tier),
            }
            for tier, name in TIER_NAMES.items()
        }
        return {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "total_cost": self._today_total,
            "total_calls": len(self._records),
            "tier_breakdown": tier_breakdown,
            "vs_pure_cloud_saving": self._estimate_saving(),
        }

    def get_monthly_summary(self) -> dict[str, Any]:
        today = datetime.now(timezone.utc)
        month_start = today.replace(day=1)
        month_records = [r for r in self._records if r.timestamp >= month_start.isoformat()]
        return {
            "year_month": today.strftime("%Y-%m"),
            "total_cost": self._month_total,
            "total_calls": len(month_records),
            "days_tracked": (today - month_start).days + 1,
        }

    def _estimate_saving(self) -> dict[str, Any]:
        """估算对比纯云端方案节省的成本。"""
        pure_cloud = self._month_total
        if self._records:
            # 模拟纯云端成本：每次都走 high_cloud
            high_cost = _DEFAULT_TIER_COST[TIER_HIGH_CLOUD]
            pure_cloud = len(self._records) * high_cost
        return {
            "pure_cloud_estimated": round(pure_cloud, 2),
            "actual_cost": round(self._month_total, 2),
            "savings_percent": round((1 - self._month_total / max(pure_cloud, 0.001)) * 100, 1),
        }

    def _persist(self) -> None:
        """持久化成本记录到 data_store。"""
        if self._data_store is None:
            return
        try:
            existing = self._data_store.load_json("cost_tracker_records", [])
            if isinstance(existing, list):
                # 保留最近 1000 条
                combined = existing + [r.to_dict() for r in self._records[-5:]]
                self._data_store.save_json("cost_tracker_records", combined[-1000:])
        except Exception as exc:
            logger.warning("持久化成本记录失败: %s", exc, exc_info=True)

    def get_status_snapshot(self) -> dict[str, Any]:
        return {
            "today_cost": self._today_total,
            "month_cost": self._month_total,
            "total_calls": len(self._records),
            "tier_breakdown": {
                name: round(self._tier_totals.get(tier, 0.0), 6)
                for tier, name in TIER_NAMES.items()
            },
        }
