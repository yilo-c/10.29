# -*- coding: utf-8 -*-
"""Budget manager — token and API cost tracking with budget limits.

Tracks daily and monthly token/API usage, enforces budget caps,
and provides spending insights for cost-aware task routing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.assistant.budget_manager")


@dataclass
class BudgetLimit:
    """预算限制配置。"""
    daily_tokens: int = 100_000
    daily_cost_usd: float = 5.0
    monthly_cost_usd: float = 100.0
    daily_api_calls: int = 500

    def to_dict(self) -> dict[str, Any]:
        return {
            "daily_tokens": self.daily_tokens,
            "daily_cost_usd": self.daily_cost_usd,
            "monthly_cost_usd": self.monthly_cost_usd,
            "daily_api_calls": self.daily_api_calls,
        }


@dataclass
class UsageRecord:
    """单次用量记录。"""
    timestamp: str
    tokens: int
    cost_usd: float
    api_calls: int
    model: str = ""
    task_id: str = ""
    category: str = "general"  # general, chat, task, mcp, etc.

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "api_calls": self.api_calls,
            "model": self.model,
            "task_id": self.task_id,
            "category": self.category,
        }


@dataclass
class BudgetStatus:
    """当前预算状态快照。"""
    date_str: str
    tokens_used: int = 0
    cost_usd: float = 0.0
    api_calls: int = 0
    tokens_remaining: int = 0
    cost_remaining: float = 0.0
    api_calls_remaining: int = 0
    within_budget: bool = True
    limit: BudgetLimit = field(default_factory=BudgetLimit)
    monthly_cost_usd: float = 0.0
    monthly_limit_usd: float = 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date_str,
            "tokens_used": self.tokens_used,
            "cost_usd": self.cost_usd,
            "api_calls": self.api_calls,
            "tokens_remaining": self.tokens_remaining,
            "cost_remaining": self.cost_remaining,
            "api_calls_remaining": self.api_calls_remaining,
            "within_budget": self.within_budget,
            "monthly_cost_usd": self.monthly_cost_usd,
            "monthly_limit_usd": self.monthly_limit_usd,
        }


class BudgetManager:
    """预算管理——跟踪 token/API 成本，防止超额。

    Usage:
        manager = BudgetManager(limit=BudgetLimit(daily_tokens=200000))
        if manager.check_budget(estimated_tokens=5000):
            manager.record_usage(tokens=5000, cost_usd=0.015)
    """

    def __init__(
        self,
        limit: BudgetLimit | None = None,
        data_store=None,
    ):
        self._limit = limit or BudgetLimit()
        self._data_store = data_store
        self._daily_usage: dict[str, list[UsageRecord]] = {}  # date_str -> records
        self._monthly_cache: dict[str, float] = {}            # month_str -> total cost

    def set_limit(self, limit: BudgetLimit) -> None:
        self._limit = limit

    def get_limit(self) -> BudgetLimit:
        return BudgetLimit(
            daily_tokens=self._limit.daily_tokens,
            daily_cost_usd=self._limit.daily_cost_usd,
            monthly_cost_usd=self._limit.monthly_cost_usd,
            daily_api_calls=self._limit.daily_api_calls,
        )

    # ── Budget check ────────────────────────────────────────────────

    def check_budget(
        self,
        estimated_tokens: int = 0,
        estimated_cost: float = 0.0,
        estimated_api_calls: int = 0,
    ) -> bool:
        """检查是否在预算内。

        Args:
            estimated_tokens: 预估 token 消耗。
            estimated_cost: 预估 USD 成本。
            estimated_api_calls: 预估 API 调用次数。

        Returns:
            True 如果在预算内，False 如果超出任何限制。
        """
        today = date.today().isoformat()
        daily = self._get_daily_usage(today)

        # Token check
        if estimated_tokens > 0:
            if daily["tokens"] + estimated_tokens > self._limit.daily_tokens:
                logger.info("Budget check FAILED: tokens %d + %d > %d",
                            daily["tokens"], estimated_tokens, self._limit.daily_tokens)
                return False

        # Cost check
        if estimated_cost > 0:
            if daily["cost"] + estimated_cost > self._limit.daily_cost_usd:
                logger.info("Budget check FAILED: cost %.4f + %.4f > %.4f",
                            daily["cost"], estimated_cost, self._limit.daily_cost_usd)
                return False

        # API call check
        if estimated_api_calls > 0:
            if daily["api_calls"] + estimated_api_calls > self._limit.daily_api_calls:
                logger.info("Budget check FAILED: api_calls %d + %d > %d",
                            daily["api_calls"], estimated_api_calls, self._limit.daily_api_calls)
                return False

        # Monthly cost check
        if estimated_cost > 0:
            monthly = self._get_monthly_cost()
            if monthly + estimated_cost > self._limit.monthly_cost_usd:
                logger.info("Budget check FAILED: monthly %.4f + %.4f > %.4f",
                            monthly, estimated_cost, self._limit.monthly_cost_usd)
                return False

        return True

    # ── Usage recording ─────────────────────────────────────────────

    def record_usage(
        self,
        tokens: int = 0,
        cost_usd: float = 0.0,
        api_calls: int = 1,
        model: str = "",
        task_id: str = "",
        category: str = "general",
    ) -> UsageRecord:
        """记录实际用量。

        Returns:
            创建的 UsageRecord。
        """
        now = datetime.now(timezone.utc).isoformat()
        record = UsageRecord(
            timestamp=now,
            tokens=tokens,
            cost_usd=cost_usd,
            api_calls=api_calls,
            model=model,
            task_id=task_id,
            category=category,
        )

        today = date.today().isoformat()
        if today not in self._daily_usage:
            self._daily_usage[today] = []
        self._daily_usage[today].append(record)

        # Update monthly cache
        month_key = date.today().strftime("%Y-%m")
        self._monthly_cache[month_key] = self._monthly_cache.get(month_key, 0.0) + cost_usd

        # Persist if data_store available
        if self._data_store is not None:
            self._persist_record(record)

        return record

    # ── Status ───────────────────────────────────────────────────────

    def get_status(self) -> BudgetStatus:
        """获取当前预算状态快照。"""
        today = date.today().isoformat()
        daily = self._get_daily_usage(today)

        tokens_remaining = max(0, self._limit.daily_tokens - daily["tokens"])
        cost_remaining = max(0.0, self._limit.daily_cost_usd - daily["cost"])
        api_remaining = max(0, self._limit.daily_api_calls - daily["api_calls"])
        monthly_cost = self._get_monthly_cost()

        within_budget = (
            daily["tokens"] <= self._limit.daily_tokens
            and daily["cost"] <= self._limit.daily_cost_usd
            and daily["api_calls"] <= self._limit.daily_api_calls
            and monthly_cost <= self._limit.monthly_cost_usd
        )

        return BudgetStatus(
            date_str=today,
            tokens_used=int(daily["tokens"]),
            cost_usd=round(float(daily["cost"]), 4),
            api_calls=int(daily["api_calls"]),
            tokens_remaining=int(tokens_remaining),
            cost_remaining=round(cost_remaining, 4),
            api_calls_remaining=int(api_remaining),
            within_budget=within_budget,
            limit=self.get_limit(),
            monthly_cost_usd=round(monthly_cost, 4),
            monthly_limit_usd=self._limit.monthly_cost_usd,
        )

    def get_daily_breakdown(self, target_date: str | None = None) -> dict[str, Any]:
        """获取指定日期（或今天）的用量分类明细。"""
        today = target_date or date.today().isoformat()
        records = self._daily_usage.get(today, [])
        breakdown: dict[str, dict[str, float]] = {}
        for r in records:
            cat = r.category
            if cat not in breakdown:
                breakdown[cat] = {"tokens": 0, "cost": 0.0, "api_calls": 0}
            breakdown[cat]["tokens"] += r.tokens
            breakdown[cat]["cost"] += r.cost_usd
            breakdown[cat]["api_calls"] += r.api_calls

        return {
            "date": today,
            "categories": {k: {sk: round(sv, 4) if isinstance(sv, float) else sv for sk, sv in v.items()} for k, v in breakdown.items()},
            "total_records": len(records),
        }

    def estimate_task_cost(self, estimated_tokens: int, model: str = "local") -> float:
        """根据 token 数和模型估算任务成本。"""
        rate_per_token = {
            "local": 0.0,
            "sonnet": 0.000003,
            "opus": 0.000015,
            "haiku": 0.0000015,
            "gpt4": 0.00001,
        }
        rate = rate_per_token.get(model, 0.000003)
        return round(estimated_tokens * rate, 6)

    def reset_daily(self) -> None:
        """重置每日用量（新的一天开始）。"""
        today = date.today().isoformat()
        self._daily_usage[today] = []

    # ── Persistence ─────────────────────────────────────────────────

    def _persist_record(self, record: UsageRecord) -> None:
        """持久化用量记录到 data_store。

        D22-1 止血①：改接 append_to_list（O(1) SQL 层原地追加，
        cap=10000 保尾部，与原 ``[-10000:]`` 截尾语义等价），
        消除整值浅拷贝+重序列化的写放大。
        """
        try:
            self._data_store.append_to_list(
                "assistant_budget_usage", record.to_dict(), cap=10000
            )
        except Exception as exc:
            logger.warning("budget record persist failed: %s", exc, exc_info=True)

    def load_history(self, days: int = 30) -> list[UsageRecord]:
        """从持久化存储加载历史用量。"""
        if self._data_store is None:
            return []
        try:
            items = self._data_store.load_json("assistant_budget_usage", [])
            if not isinstance(items, list):
                return []
            cutoff = datetime.now(timezone.utc)
            from datetime import timedelta
            cutoff = cutoff - timedelta(days=max(1, days))
            records = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                ts = str(item.get("timestamp", ""))
                try:
                    # ★ 修复时区剥离 bug：原代码 .replace("Z","+00:00").replace("+00:00","")
                    # 会把 UTC 时间戳的时区信息再次剥掉变成 naive datetime，
                    # 随后与 aware 的 cutoff 比较抛 TypeError（非 ValueError），
                    # 被外层 except Exception 捕获后返回空列表 → 预算历史全部丢失。
                    normalized = ts.replace("Z", "+00:00")
                    item_dt = datetime.fromisoformat(normalized)
                    if item_dt.tzinfo is None:
                        item_dt = item_dt.replace(tzinfo=timezone.utc)
                    if item_dt < cutoff:
                        continue
                except ValueError:
                    logger.debug("Non-critical error (see details)", exc_info=True)

                records.append(UsageRecord(
                    timestamp=ts,
                    tokens=int(item.get("tokens", 0)),
                    cost_usd=float(item.get("cost_usd", 0.0)),
                    api_calls=int(item.get("api_calls", 0)),
                    model=str(item.get("model", "")),
                    task_id=str(item.get("task_id", "")),
                    category=str(item.get("category", "general")),
                ))
            return records
        except Exception as exc:
            logger.warning("budget history load failed: %s", exc, exc_info=True)
            return []

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_daily_usage(self, date_str: str) -> dict[str, int | float]:
        tokens = 0
        cost = 0.0
        api_calls = 0
        for record in self._daily_usage.get(date_str, []):
            tokens += record.tokens
            cost += record.cost_usd
            api_calls += record.api_calls
        return {"tokens": tokens, "cost": cost, "api_calls": api_calls}

    def _get_monthly_cost(self) -> float:
        month_key = date.today().strftime("%Y-%m")
        return self._monthly_cache.get(month_key, 0.0)
