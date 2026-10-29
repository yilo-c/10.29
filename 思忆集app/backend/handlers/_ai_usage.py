"""AI usage, cost, and aggregation telemetry JSON-RPC handlers.

从原 _ai.py 拆分而来（用量与成本可观测域）：
  - aggregation.stats / aggregation.logs / aggregation.config
  - usage.stats / usage.records / usage.budget.get / usage.budget.set /
    usage.check_budget / usage.dashboard / usage.today / usage.export / usage.purge

usage.* 与 aggregation.* 都依赖 ai_manager 内部遥测（aggregation_telemetry /
aggregator / cost_tracker）以及 ai.usage_tracker 全局单例，归并到同一模块以保持
遥测访问路径一致。函数内延迟 import 随方法迁移，避免循环依赖。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from backend.handlers._list_guard import clamp_limit

logger = logging.getLogger("siyi.backend.handlers.ai_usage")


def register_ai_usage(registry, context) -> None:
    """Register AI usage / cost / aggregation telemetry JSON-RPC methods."""

    # ── aggregation.stats / aggregation.logs ──
    async def aggregation_stats(days: int = 1, **kwargs: Any) -> dict[str, Any]:
        """返回 API 聚合层最近 N 天的统计。"""
        ai_manager = getattr(context, "ai_manager", None)
        telemetry = getattr(ai_manager, "aggregation_telemetry", None)
        if telemetry is None:
            return {"enabled": False, "error": "aggregation telemetry unavailable"}
        since = time.time() - days * 24 * 3600
        return {"enabled": True, "days": days, **telemetry.stats(since=since)}

    registry.register("aggregation.stats", aggregation_stats)

    async def aggregation_logs(
        limit: int = 50,
        offset: int = 0,
        strategy: str = "",
        winner: str = "",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """返回 API 聚合层调用日志。"""
        ai_manager = getattr(context, "ai_manager", None)
        telemetry = getattr(ai_manager, "aggregation_telemetry", None)
        if telemetry is None:
            return []
        # ★ 包裹 to_thread：telemetry.query 执行 SQLite 查询，虽是内存数据库
        # 但保持与 codebase 其余 I/O 调用一致的非阻塞模式。
        return await asyncio.to_thread(
            telemetry.query,
            limit=clamp_limit(limit, default=50),
            offset=offset,
            strategy=strategy or None,
            winner=winner or None,
        )

    registry.register("aggregation.logs", aggregation_logs)

    async def aggregation_config(**kwargs: Any) -> dict[str, Any]:
        """返回当前聚合层配置快照。"""
        ai_manager = getattr(context, "ai_manager", None)
        aggregator = getattr(ai_manager, "aggregator", None)
        if aggregator is None:
            return {"enabled": False}
        return {
            "enabled": True,
            "strategy": str(aggregator._default_strategy.value),
            "timeout_ms": aggregator._default_timeout_ms,
            "max_concurrent": aggregator._max_concurrent,
            "providers": [
                {
                    "provider_name": p.provider_name,
                    "model": p.model,
                    "tier": p.tier,
                    "cost_per_1k_tokens": p.cost_per_1k_tokens,
                    "priority": p.priority,
                }
                for p in aggregator._providers
            ],
        }

    registry.register("aggregation.config", aggregation_config)

    # ── B1: usage.stats / usage.records / usage.budget.* ──

    async def usage_stats(days: int = 7, provider: str = "", **kwargs: Any) -> dict[str, Any]:
        """返回最近 N 天的 AI 用量聚合统计。

        Args:
            days: 聚合窗口天数（默认 7）
            provider: 可选 provider 过滤；空字符串表示不过滤
        """
        from ai.usage_tracker import get_usage_tracker

        tracker = get_usage_tracker()
        if tracker is None:
            return {"enabled": False, "error": "usage tracker not initialized"}
        provider_arg = provider.strip() or None
        return await asyncio.to_thread(
            tracker.get_stats,
            days=days,
            provider=provider_arg,
        )

    registry.register("usage.stats", usage_stats)

    async def usage_records(
        limit: int = 100,
        offset: int = 0,
        provider: str = "",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """返回最近 N 条用量明细记录（按时间倒序）。"""
        from ai.usage_tracker import get_usage_tracker

        tracker = get_usage_tracker()
        if tracker is None:
            return []
        # P0 修复：limit/offset 非数字时优雅降级，避免 get_records 内 int() 抛 ValueError
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 100
        # 帧守卫（2026-10-15）：limit<=0 回退默认，上限 clamp 2000
        limit = clamp_limit(limit, default=100)
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            offset = 0
        provider_arg = provider.strip() or None
        return await asyncio.to_thread(
            tracker.get_records,
            limit=limit,
            offset=offset,
            provider=provider_arg,
        )

    registry.register("usage.records", usage_records)

    async def usage_budget_get(**kwargs: Any) -> dict[str, Any]:
        """返回当前 AI 用量预算配置（日 token / cost 上限）。"""
        from ai.usage_tracker import get_usage_tracker

        tracker = get_usage_tracker()
        if tracker is None:
            return {"daily_token_limit": None, "daily_cost_limit": None}
        return await asyncio.to_thread(tracker.get_budget)

    registry.register("usage.budget.get", usage_budget_get)

    async def usage_budget_set(
        daily_token_limit: int | None = None,
        daily_cost_limit: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """配置 AI 用量日预算上限。

        Args:
            daily_token_limit: 每日 token 上限；None 不修改；0 或负数禁用该项
            daily_cost_limit: 每日 USD 成本上限；None 不修改；0 或负数禁用该项
        """
        from ai.usage_tracker import get_usage_tracker

        tracker = get_usage_tracker()
        if tracker is None:
            return {"daily_token_limit": None, "daily_cost_limit": None, "error": "tracker not initialized"}
        return await asyncio.to_thread(
            tracker.set_budget,
            daily_token_limit=daily_token_limit,
            daily_cost_limit=daily_cost_limit,
        )

    registry.register("usage.budget.set", usage_budget_set)

    async def usage_check_budget(**kwargs: Any) -> dict[str, Any]:
        """立即执行一次预算检查，返回当前用量相对预算的占比与是否超额。"""
        from ai.usage_tracker import get_usage_tracker

        tracker = get_usage_tracker()
        if tracker is None:
            return {"exceeded": False, "error": "tracker not initialized"}
        return await asyncio.to_thread(tracker.check_budget)

    registry.register("usage.check_budget", usage_check_budget)

    # ──────────────────────────────────────────────────────────────
    # P2.7: 成本可观测 Dashboard
    # ──────────────────────────────────────────────────────────────

    # ── usage.dashboard ──
    # 一次性返回构建成本仪表盘所需的全部数据
    async def usage_dashboard(days: int = 7, **kwargs: Any) -> dict[str, Any]:
        from ai.usage_tracker import get_usage_tracker

        clamped_days = min(max(int(days), 1), 90)
        tracker = get_usage_tracker()
        if tracker is None:
            return {
                "enabled": False,
                "error": "usage tracker not initialized",
                "today": None,
                "stats": None,
                "budget": None,
                "provider_health": [],
                "tier_breakdown": None,
            }

        # 1. 今日统计（用 days=1 + filter provider=None）
        stats_1d = await asyncio.to_thread(tracker.get_stats, 1, None)
        today_data = {
            "calls": stats_1d.get("total_calls", 0),
            "tokens": stats_1d.get("total_tokens", 0),
            "cost": stats_1d.get("total_cost", 0.0),
            "by_provider": stats_1d.get("by_provider", []),
        }

        # 2. N 天聚合统计
        stats_n = await asyncio.to_thread(tracker.get_stats, clamped_days, None)

        # 3. 预算状态
        budget_status = await asyncio.to_thread(tracker.check_budget)

        # 4. Provider 健康状态（来自 ProviderHealthMonitor，可能为 None）
        provider_health: list[dict[str, Any]] = []
        ai_client = getattr(context, "ai_client", None)
        inner = getattr(ai_client, "_inner", None) if ai_client else None
        health_monitor = getattr(inner, "_health_monitor", None) if inner else None
        if health_monitor is not None:
            try:
                provider_health = await asyncio.to_thread(health_monitor.get_status)
            except Exception as exc:
                logger.debug("get provider health for dashboard failed: %s", exc)

        # 5. Tier 维度成本（来自旧版 CostTracker，可能为 None）
        tier_breakdown: dict[str, Any] | None = None
        ai_manager = getattr(context, "ai_manager", None)
        cost_tracker = getattr(ai_manager, "cost_tracker", None) if ai_manager else None
        if cost_tracker is not None:
            try:
                daily = await asyncio.to_thread(cost_tracker.get_daily_summary)
                tier_breakdown = {
                    "today_cost": daily.get("total_cost", 0.0),
                    "today_calls": daily.get("total_calls", 0),
                    "tier_breakdown": daily.get("tier_breakdown", {}),
                    "vs_pure_cloud_saving": daily.get("vs_pure_cloud_saving", 0.0),
                }
            except Exception as exc:
                logger.debug("get tier breakdown for dashboard failed: %s", exc)

        # 6. 当日告警状态（已推送过哪些告警类型）
        alert_state_today: dict[str, bool] = {}
        try:
            from datetime import date as _date

            today_str = _date.today().isoformat()
            with tracker._alert_state_lock:
                alert_state_today = dict(tracker._alert_state.get(today_str, {}))
        except Exception:
            logger.exception("获取当日告警状态失败")
            pass

        return {
            "enabled": True,
            "days": clamped_days,
            "today": today_data,
            "stats": stats_n,
            "budget": budget_status,
            "provider_health": provider_health,
            "tier_breakdown": tier_breakdown,
            "alert_state_today": alert_state_today,
        }

    registry.register("usage.dashboard", usage_dashboard)

    # ── usage.today ──
    # 今日专用的轻量端点（只返回今日调用/token/cost + by_provider）
    async def usage_today(**kwargs: Any) -> dict[str, Any]:
        from ai.usage_tracker import get_usage_tracker

        tracker = get_usage_tracker()
        if tracker is None:
            return {"enabled": False, "error": "usage tracker not initialized"}
        stats = await asyncio.to_thread(tracker.get_stats, 1, None)
        return {
            "enabled": True,
            "calls": stats.get("total_calls", 0),
            "tokens": stats.get("total_tokens", 0),
            "cost": stats.get("total_cost", 0.0),
            "by_provider": stats.get("by_provider", []),
            "by_model": stats.get("by_model", []),
        }

    # @deprecated (S11, 2026-08-07): 僵尸接口，前端未调用。见 UNUSED_METHODS_BASELINE。
    registry.register("usage.today", usage_today)

    # ── usage.export ──
    # 导出最近 N 天的明细记录为 CSV 字符串
    async def usage_export(days: int = 7, **kwargs: Any) -> dict[str, Any]:
        import csv
        import io
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from ai.usage_tracker import get_usage_tracker

        clamped_days = min(max(int(days), 1), 90)
        tracker = get_usage_tracker()
        if tracker is None:
            return {"enabled": False, "error": "usage tracker not initialized"}

        # 拉取最近 N 天的全部记录（limit 上限 1000）
        records = await asyncio.to_thread(tracker.get_records, 1000, 0, None)
        # 在内存中按 timestamp 过滤（tracker.get_records 不支持日期过滤）
        cutoff = (_dt.utcnow() - _td(days=clamped_days)).isoformat()
        filtered = [r for r in records if r.get("timestamp", "") >= cutoff]

        # 生成 CSV
        output = io.StringIO()
        if filtered:
            writer = csv.DictWriter(output, fieldnames=list(filtered[0].keys()))
            writer.writeheader()
            for row in filtered:
                writer.writerow(row)
        csv_content = output.getvalue()
        return {
            "enabled": True,
            "days": clamped_days,
            "count": len(filtered),
            "csv": csv_content,
        }

    registry.register("usage.export", usage_export)

    # ── usage.purge ──
    # 清理 N 天前的旧记录（默认 30 天）
    async def usage_purge(older_than_days: int = 30, **kwargs: Any) -> dict[str, Any]:
        from ai.usage_tracker import get_usage_tracker

        clamped_days = min(max(int(older_than_days), 1), 365)
        tracker = get_usage_tracker()
        if tracker is None:
            return {"enabled": False, "error": "usage tracker not initialized"}

        # 直接执行 DELETE SQL（UsageTracker 暴露 _db_path 和线程本地连接）
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        cutoff = (_dt.utcnow() - _td(days=clamped_days)).isoformat()
        deleted = await asyncio.to_thread(tracker.purge_before, cutoff)
        return {
            "enabled": True,
            "older_than_days": clamped_days,
            "deleted": deleted,
            "cutoff": cutoff,
        }

    registry.register("usage.purge", usage_purge)

    # ── T7.5: usage.monthly_report ──
    # 月度成本摘要：聚合近 31 天用量生成可读摘要文本，可选投递收件箱。
    # 前提实证：cron 路径（narrative/info collect 等）统一走 context.ai_client
    # （JudgeAwareClient 包装 _inner_client，含 BudgetManager 计量），
    # 因此月度报告天然覆盖 cron 产生的 AI 成本，无需单独对账。
    async def usage_monthly_report(post_to_inbox: bool = False, **kwargs: Any) -> dict[str, Any]:
        """生成月度 AI 成本摘要（近 31 天）。

        Args:
            post_to_inbox: 是否同时投递到收件箱（生成一封月度成本信）

        Returns:
            {month_label, summary(文本), stats, budget, posted}
        """
        from ai.usage_tracker import get_usage_tracker
        from datetime import datetime as _dt

        tracker = get_usage_tracker()
        if tracker is None:
            return {"enabled": False, "error": "usage tracker not initialized"}

        stats = await asyncio.to_thread(tracker.get_stats, 31, None)
        budget = await asyncio.to_thread(tracker.check_budget)

        month_label = _dt.utcnow().strftime("%Y-%m")
        total_cost = float(stats.get("total_cost", 0.0))
        total_calls = int(stats.get("total_calls", 0))
        total_tokens = int(stats.get("total_tokens", 0))
        by_provider = stats.get("by_provider", [])

        # 生成可读摘要
        lines = [f"{month_label} AI 用量回顾："]
        lines.append(f"- 调用 {total_calls} 次，Token {total_tokens}，成本 ¥{total_cost:.4f}")
        if by_provider:
            provider_brief = "、".join(f"{p.get('provider')}({p.get('calls', 0)}次/¥{float(p.get('cost', 0.0)):.4f})" for p in by_provider)
            lines.append(f"- 按 provider：{provider_brief}")
        budget_status = "可用" if isinstance(budget, dict) and budget.get("under_budget", True) else "已达上限"
        lines.append(f"- 预算状态：{budget_status}")
        summary = "\n".join(lines)

        posted = False
        if post_to_inbox:
            inbox_repo = getattr(context, "personal_inbox_repository", None)
            if inbox_repo is not None and hasattr(inbox_repo, "add_entry"):
                inbox_repo.add_entry(
                    {
                        "title": f"💸 {month_label} AI 成本月报",
                        "content": summary,
                        "kind": "narrative",
                        "tags": ["成本月报", "自动生成", month_label],
                        "status": "new",
                        "metadata": {"month_label": month_label, "total_cost": total_cost},
                    }
                )
                posted = True

        return {
            "enabled": True,
            "month_label": month_label,
            "summary": summary,
            "stats": stats,
            "budget": budget,
            "posted": posted,
        }

    registry.register("usage.monthly_report", usage_monthly_report)
