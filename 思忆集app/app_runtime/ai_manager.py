"""AI 管理域：AI 客户端创建/重建、CostTracker、BudgetManager、JudgeScheduler、LocalAIRouter。"""

from __future__ import annotations

import logging

from ai.client.base import OpenAICompatibleClient
from ai.client.config import create_fallback_client, is_ai_cloud_only, resolve_local_coder_openai_compatible_config
from ai.client import JudgeAwareClient
from ai.aggregator import ApiAggregator, ProviderTier, AggregationStrategy
from ai.provider_quota import ProviderQuota
from ai.aggregation_telemetry import AggregationTelemetry
from assistant.cost_tracker import CostTracker
from assistant.budget_manager import BudgetManager
from assistant.judge import JudgeScheduler
from assistant.local_router import LocalAIRouter
from typing import Any

logger = logging.getLogger("siyi.ai_manager")


class AiManager:
    """管理 AI 客户端的生命周期、路由器和统计信息。

    DI 层级：
      _inner_client (FallbackOpenAIClient) — 原始传输层 (primary→fallback→local→cache)
      local_ai_router (LocalAIRouter)       — 本地 AI 优先路由
      judge_scheduler (JudgeScheduler)      — Judge 路由决策
      ai_client (JudgeAwareClient)          — 公开接口，包装全部
    """

    def __init__(self, data_store, settings_repository) -> None:
        self.cost_tracker = CostTracker(data_store=data_store)
        self.budget_manager = BudgetManager(data_store=data_store)

        # 0. 本地 Coder AI 客户端（qwen2.5-coder，代码专用）
        coder_cfg = resolve_local_coder_openai_compatible_config(
            settings_repository=settings_repository,
        )
        coder_client: OpenAICompatibleClient | None = None
        if coder_cfg.base_url:
            coder_client = OpenAICompatibleClient(
                api_key=coder_cfg.api_key or "",
                api_base=coder_cfg.base_url,
                model=coder_cfg.model or "qwen2.5-coder",
                timeout=coder_cfg.timeout or 60,
            )

        # 1. 原始传输层客户端 (primary → fallback → local → local_coder → cache)
        self._inner_client = create_fallback_client(
            settings_repository=settings_repository,
            cost_tracker=self.cost_tracker,
            budget_manager=self.budget_manager,
            local_coder=coder_client,
        )

        # 2. LocalAIRouter（使用 _inner_client 的 local 子客户端）
        self.local_ai_router = LocalAIRouter(
            ai_client=self._inner_client,
            settings_repository=settings_repository,
        )

        # 3. JudgeScheduler（使用 _inner_client 做 cloud LLM 路由）
        # P1-1: 接入成本监管 —— Judge 路由决策前 check_budget，决策后 record 成本
        self.judge_scheduler = JudgeScheduler(
            ai_client=self._inner_client,
            cost_tracker=self.cost_tracker,
            budget_manager=self.budget_manager,
            local_router=self.local_ai_router,
            cloud_only=is_ai_cloud_only(settings_repository),
        )

        # 4. API 聚合层（可选开启）
        self.aggregation_telemetry = AggregationTelemetry()
        self.aggregator = self._create_aggregator(settings_repository)

        # 5. 公开 AI 客户端（JudgeAwareClient 包装全部）
        # ★ P0 修复：必须传入 settings_repository，否则 get_client_by_provider()
        # 无法加载 provider 配置，导致用户显式选择的模型（如 DeepSeek）被静默忽略，
        # 回退到 Judge 路由 → 本地 Ollama（未运行时浪费 14s 重试）→ 超时
        self.ai_client = JudgeAwareClient(
            inner_client=self._inner_client,
            settings_repository=settings_repository,
            judge=self.judge_scheduler,
            local_ai_router=self.local_ai_router,
            aggregator=self.aggregator,
        )

    def recreate_ai_client(self, settings_repository) -> object:
        """从当前设置重新创建 AI 客户端，更新所有本地引用。"""
        coder_cfg = resolve_local_coder_openai_compatible_config(
            settings_repository=settings_repository,
        )
        coder_client: OpenAICompatibleClient | None = None
        if coder_cfg.base_url:
            coder_client = OpenAICompatibleClient(
                api_key=coder_cfg.api_key or "",
                api_base=coder_cfg.base_url,
                model=coder_cfg.model or "qwen2.5-coder",
                timeout=coder_cfg.timeout or 60,
            )

        new_inner = create_fallback_client(
            settings_repository=settings_repository,
            cost_tracker=self.cost_tracker,
            budget_manager=self.budget_manager,
            local_coder=coder_client,
        )
        logger.info(
            "recreating AI client (api_key present: %s)",
            bool(new_inner.primary.api_key if hasattr(new_inner, "primary") else False),
        )

        self._inner_client = new_inner
        self.local_ai_router.ai_client = new_inner  # type: ignore[attr-defined]
        self.judge_scheduler._ai_client = new_inner
        self.aggregation_telemetry = AggregationTelemetry()
        self.aggregator = self._create_aggregator(settings_repository)

        self.ai_client = JudgeAwareClient(
            inner_client=new_inner,
            settings_repository=settings_repository,
            judge=self.judge_scheduler,
            local_ai_router=self.local_ai_router,
            aggregator=self.aggregator,
        )
        return self.ai_client

    def _create_aggregator(self, settings_repository) -> ApiAggregator | None:
        """根据设置创建 API 聚合层；未启用或配置无效时返回 None。"""
        if settings_repository is None:
            return None
        try:
            from core.settings_keys import (
                KEY_AGGREGATION_ENABLED,
                KEY_AGGREGATION_STRATEGY,
                KEY_AGGREGATION_TIMEOUT_MS,
                KEY_AGGREGATION_MAX_PROVIDERS,
                KEY_AGGREGATION_PROVIDERS,
            )

            enabled = bool(settings_repository.get_setting(KEY_AGGREGATION_ENABLED, False))
            if not enabled:
                return None

            strategy_str = settings_repository.get_setting(KEY_AGGREGATION_STRATEGY, "race")
            strategy = AggregationStrategy(strategy_str)
            timeout_ms = int(settings_repository.get_setting(KEY_AGGREGATION_TIMEOUT_MS, 15_000))
            max_providers = int(settings_repository.get_setting(KEY_AGGREGATION_MAX_PROVIDERS, 3))
            agg_providers = list(settings_repository.get_setting(KEY_AGGREGATION_PROVIDERS, []))

            # 未显式配置聚合 provider 时，从 provider_configs 中自动选取前 N 个启用的 provider
            if not agg_providers:
                from core.provider_config import load_providers

                all_providers = load_providers(settings_repository)
                agg_providers = [
                    {
                        "provider_name": p.name,
                        "model": p.model or p.name,
                        "tier": "cheap_cloud" if p.name.lower() not in ("ollama", "local") else "local",
                        "cost_per_1k_tokens": 0.0 if p.name.lower() in ("ollama", "local") else 0.5,
                        "priority": idx,
                    }
                    for idx, p in enumerate(all_providers)
                    if p.enabled
                ][:max_providers]

            if not agg_providers:
                logger.warning("aggregation enabled but no providers configured")
                return None

            provider_tiers = [
                ProviderTier(
                    provider_name=str(p.get("provider_name") or p.get("name") or ""),
                    model=str(p.get("model") or p.get("provider_name") or ""),
                    tier=str(p.get("tier") or "cloud"),
                    cost_per_1k_tokens=float(p.get("cost_per_1k_tokens", 0.5)),
                    priority=int(p.get("priority", 0)),
                )
                for p in agg_providers
                if p.get("provider_name") or p.get("name")
            ]

            quota = ProviderQuota()
            for p in provider_tiers:
                # ★ 修复 `and None or` 反模式：
                #   `x and None or y` 当 x 为真时 → `(True and None) or y` = y（错）
                #   本意是免费 provider（cost==0.0）应得到 None（不限额），
                #   实际却被赋予 1_000_000 限额。改用显式三元表达式。
                quota.set_config(
                    p.provider_name,
                    {
                        "daily_token_limit": None if p.cost_per_1k_tokens == 0.0 else 1_000_000,
                        "rate_limit_rpm": 60,
                    },
                )

            return ApiAggregator(
                client_factory=self._inner_client.get_client_by_provider,
                providers=provider_tiers,
                quota_manager=quota,
                telemetry=self.aggregation_telemetry,
                default_strategy=strategy,
                default_timeout_ms=timeout_ms,
                max_concurrent=max_providers,
            )
        except Exception as exc:
            logger.warning("failed to create aggregator: %s", exc)
            return None

    def process_with_local_router(self, user_input: str, context: dict | None = None) -> Any:
        return self.local_ai_router.process(user_input, context)

    def judge_route_task(self, task: str, context: dict | None = None) -> Any:
        return self.judge_scheduler.route(task, context)

    def get_judge_stats(self) -> dict:
        return self.judge_scheduler.get_stats()

    def get_local_router_status(self) -> dict:
        return self.local_ai_router.get_status_snapshot()
