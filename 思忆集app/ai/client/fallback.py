"""FallbackOpenAIClient — 三阶段回退 AI 客户端。

从 ai_service.py 提取，职责：
- primary → fallback → local → cache 回退链
- JudgeAwareClient 已提取到 _judge_aware_client.py

缓存并发安全：
- 使用 asyncio.Lock 替代 threading.RLock
- 原子写入：先写临时文件，再重命名
"""

from __future__ import annotations

import asyncio
import logging
import os
import contextvars
import threading
import time
import uuid
from pathlib import Path
from collections.abc import AsyncIterator
from typing import Any, Callable

from ai._llm_path import PROJECT_ROOT  # noqa: F401 — ensures project root on sys.path
from ai.client._cache import AiResponseCache

# ContextVar 隔离并发流式调用的 tool_calls（替代单例实例属性 self._last_stream_tool_calls）
_last_stream_tool_calls_var: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar("_last_stream_tool_calls", default=[])

from ai.circuit_breaker import get_circuit_breaker_registry
from ai.llm_resilience import build_observability_tags
from ai.receipt import ModelInvocationReceipt, make_receipt
from ai.aggregation_telemetry import AggregationTelemetry
from ai.provider_health_monitor import ProviderHealthMonitor
from ai.types import AIServiceError

from .base import OpenAICompatibleClient

logger = logging.getLogger("siyi.ai.fallback")


def _async_iter_from_sync(sync_iter_factory: Any) -> Any:
    """将同步生成器工厂包装为异步生成器，避免在事件循环中阻塞。

    用于 fallback.py 的流式方法：在后台线程中迭代同步生成器，并通过
    asyncio.Queue 异步 yield。如果迭代过程中抛出异常，会在消费端重新抛出。
    """
    import contextlib
    import threading

    async def _wrapper() -> AsyncIterator[Any]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=64)
        sentinel = object()
        exception_container: list[BaseException] = []

        def _run() -> None:
            try:
                with contextlib.closing(sync_iter_factory()) as gen:
                    for item in gen:
                        future = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
                        future.result()
            except BaseException as exc:  # noqa: BLE001
                exception_container.append(exc)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(sentinel), loop)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                yield item
        finally:
            thread.join(timeout=5)

        if exception_container:
            raise exception_container[0]

    return _wrapper()


# P2-16: AI 响应缓存默认 TTL 从 24h 调短到 1h。
# 24h 偏长：上游短暂故障期间用户会反复拿到一天前的"陈旧"答案。
# 1h 兼顾 hit 率与新鲜度；可通过 ``SIYI_AI_CACHE_TTL`` 覆盖。
_DEFAULT_CACHE_TTL_SECONDS = 3600


def _resolve_default_cache_ttl() -> int:
    raw = os.environ.get("SIYI_AI_CACHE_TTL")
    if raw is None or raw == "":
        return _DEFAULT_CACHE_TTL_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_CACHE_TTL_SECONDS
    return max(0, value)


def _resolve_default_cache_max_entries() -> int:
    raw = os.environ.get("SIYI_AI_CACHE_MAX")
    if raw is None or raw == "":
        return 500
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 500
    return max(0, value)


# ── FallbackOpenAIClient ──────────────────────────────


class FallbackOpenAIClient:
    """包装 OpenAICompatibleClient，提供本地 LLM 与文件缓存的回退策略。

    使用顺序：主服务器 -> fallback（可选）-> 本地 LLM（可选）-> 本地缓存。
    缓存按提示哈希保存到 JSON 文件 `data/ai_cache.json`。
    """

    def __init__(
        self,
        primary: OpenAICompatibleClient,
        fallback: OpenAICompatibleClient | None = None,
        local: OpenAICompatibleClient | None = None,
        local_coder: OpenAICompatibleClient | None = None,
        vision_client: OpenAICompatibleClient | None = None,  # purpose=vision 的专用视觉 provider
        cache_path: str | None = None,
        cost_tracker=None,
        budget_manager=None,
        cache_ttl_seconds: int | None = None,
        cache_max_entries: int | None = None,
        privacy_filter: Callable[[str], str] | None = None,  # 返回 "local_only" / "shareable"
        receipt_callback: Callable[[ModelInvocationReceipt], Any] | None = None,
        telemetry: AggregationTelemetry | None = None,  # 全链路追踪
        health_monitor: ProviderHealthMonitor | None = None,  # 调用前跳过已降级 provider (B2)
        stage_provider_names: dict[str, str] | None = None,  # stage -> provider name 映射，用于 health check
        cloud_only: bool = False,  # 云化模式：隐私守卫永远放行云端（用户已同意数据上云）
        local_first: bool = False,  # 本地优先：本地腿先行、云端兜底（默认 False=云端优先现状；优先级高于 cloud_only）
    ):
        self.primary = primary
        self.fallback = fallback
        self.local = local
        self.local_coder = local_coder
        self.vision_client = vision_client
        # P2-16: 默认 1h，环境变量 SIYI_AI_CACHE_TTL/SIYI_AI_CACHE_MAX 可覆盖。
        if cache_ttl_seconds is None:
            cache_ttl_seconds = _resolve_default_cache_ttl()
        if cache_max_entries is None:
            cache_max_entries = _resolve_default_cache_max_entries()
        # 2026-09-16 修复隔离泄漏：原实现硬编码 `<repo>/思忆集app/data/ai_cache.json`，
        # 无视 SIYI_DATA_DIR → E2E/集成测试的隔离后端仍把缓存写进**真实源码树**
        # （实测：E2E 跑动时写 data/ai_cache.json 与 .ai_cache_*.tmp，被沙箱拦下；
        # 无沙箱环境下会真写用户数据）。改与 data/local_storage.py:152 同范式。
        if cache_path:
            cache_path_str = cache_path
        else:
            _data_dir_env = os.environ.get("SIYI_DATA_DIR", "").strip()
            _base = _data_dir_env or str(Path(__file__).parent.parent.parent.joinpath("data"))
            cache_path_str = str(Path(_base, "ai_cache.json"))
        self._cache_obj = AiResponseCache(
            cache_path=cache_path_str,
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_max_entries,
        )
        self.cache_path = self._cache_obj.cache_path
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_max_entries = cache_max_entries
        self._dynamic_clients_lock = threading.RLock()
        self.cost_tracker = cost_tracker
        self.budget_manager = budget_manager
        if privacy_filter is not None:
            self._privacy_filter = privacy_filter
        else:
            from ai._assistant_bridge import PrivacyClassifier

            self._privacy_filter = PrivacyClassifier().classify
        self._receipt_callback = receipt_callback
        # S1+S2: 可选的 AggregationTelemetry，用于记录每次 AI 调用的链路追踪。
        # 不传入时所有 _trace_call 调用为 no-op。
        self._telemetry = telemetry
        # B2: 可选的 ProviderHealthMonitor，用于在调用前跳过已降级 provider。
        # 不传入时所有 _is_stage_available 调用返回 True（保持向后兼容）。
        self._health_monitor = health_monitor
        self._stage_provider_names: dict[str, str] = dict(stage_provider_names or {})
        self._cloud_only = cloud_only
        self._local_first = local_first
        self._last_runtime: dict[str, Any] = {
            "last_stage": "idle",
            "degraded": False,
            "cache_hit": False,
            "last_error": "",
            "last_status": None,
            "last_model": getattr(primary, "model", None),
            "attempted_stages": [],
        }
        # _last_stream_tool_calls 改用 ContextVar（见模块顶部），实例属性仅作类型提示占位
        self._dynamic_clients: dict[str, Any] | None = None
        self._settings_for_providers: object = None

    # ── S1+S2: 链路追踪辅助 ──

    def _trace_call(
        self,
        *,
        correlation_id: str,
        provider_name: str,
        stage: str,
        success: bool,
        latency_ms: int | None = None,
        error: str | None = None,
        token_count: int | None = None,
    ) -> None:
        """记录一次 AI 调用的链路追踪（telemetry 未配置时为 no-op）。"""
        if self._telemetry is None:
            return
        try:
            self._telemetry.record_trace(
                correlation_id=correlation_id,
                provider_name=provider_name,
                stage=stage,
                success=success,
                latency_ms=latency_ms,
                error=error,
                token_count=token_count,
            )
        except Exception as exc:
            logger.debug("trace_call failed: %s", exc)

    @staticmethod
    def _new_correlation_id() -> str:
        """生成新的调用链路 ID。

        P1.8: 若 contextvar 中已有 trace_id（由 chat.send / agent 入口设置），
        则复用之，使一次用户请求的多次 LLM 调用共享同一 correlation_id，
        便于 aggregation_telemetry.call_traces 聚合回放。
        """
        try:
            from core.agent_context_vars import get_trace_id

            existing = get_trace_id()
            if existing:
                return existing
        except Exception:
            pass
        return uuid.uuid4().hex

    # ── B2: Provider 健康检查 hook ──

    def set_health_monitor(
        self,
        monitor: ProviderHealthMonitor | None,
        stage_provider_names: dict[str, str] | None = None,
    ) -> None:
        """注入 ProviderHealthMonitor，可选更新 stage -> provider name 映射。

        monitor 为 None 时禁用 health check（所有 stage 视为可用）。
        """
        self._health_monitor = monitor
        if stage_provider_names is not None:
            self._stage_provider_names = dict(stage_provider_names)

    def _is_stage_available(self, stage_name: str) -> bool:
        """检查某 stage 对应的 provider 是否可用。

        未注入 health_monitor、或 stage 未映射到 provider name 时返回 True（乐观）。
        """
        monitor = self._health_monitor
        if monitor is None:
            return True
        provider_name = self._stage_provider_names.get(stage_name)
        if not provider_name:
            return True
        try:
            return bool(monitor.is_available(provider_name))
        except Exception:
            logger.debug("health check failed for stage %s", stage_name, exc_info=True)
            return True

    # ── 运行时快照 ──

    @staticmethod
    def _describe_client(stage: str, client: OpenAICompatibleClient | None) -> dict[str, Any]:
        if client is None:
            return {
                "stage": stage,
                "configured": False,
                "model": None,
                "base_url": None,
            }
        return {
            "stage": stage,
            "configured": bool(client.is_configured()),
            "model": getattr(client, "model", None),
            "api_base": getattr(client, "api_base", None),
        }

    def _record_runtime(
        self,
        *,
        stage: str,
        degraded: bool,
        cache_hit: bool,
        last_error: str = "",
        last_status: int | None = None,
        last_model: str | None = None,
        attempted_stages: list[str] | None = None,
    ) -> None:
        self._last_runtime = {
            "last_stage": stage,
            "degraded": degraded,
            "cache_hit": cache_hit,
            "last_error": str(last_error or ""),
            "last_status": last_status,
            "last_model": last_model,
            "attempted_stages": list(attempted_stages or []),
        }

    def _record_ai_cost(
        self,
        stage_name: str,
        client: object | None = None,
        task_type: str = "chat_completion",
        tokens: int = 0,
    ) -> None:
        """Record cost and usage after a successful AI call."""
        if self.cost_tracker is None and self.budget_manager is None:
            return
        tier = "local" if stage_name in ("local", "local_coder") else ("mid_cloud" if stage_name == "fallback" else "high_cloud")
        model = getattr(client, "model", None) or ""
        if self.cost_tracker is not None:
            self.cost_tracker.record(tier=tier, task_type=task_type, model=model, tokens=tokens)
        if self.budget_manager is not None:
            self.budget_manager.record_usage(api_calls=1, model=model, category=task_type)

    def _emit_receipt(
        self,
        stage_name: str,
        client: object | None = None,
        status: str = "completed",
        task_type: str = "chat_completion",
        tokens_input: int = 0,
        tokens_output: int = 0,
        error: str = "",
        messages: list[dict[str, str]] | None = None,
    ) -> None:
        """Generate a ModelInvocationReceipt and invoke receipt_callback if set."""
        if self._receipt_callback is None:
            return
        try:
            tier = "local" if stage_name in ("local", "local_coder") else ("mid_cloud" if stage_name == "fallback" else "high_cloud")
            provider = self.ROUTE_PROVIDERS.get(stage_name, stage_name)
            receipt = make_receipt(
                model=getattr(client, "model", None) or "",
                provider=provider,
                tier=tier,
                task_type=task_type,
                status=status,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                error=error,
                messages=messages,
            )
            self._receipt_callback(receipt)
        except Exception as exc:
            logger.debug("receipt callback failed: %s", exc)

    def get_runtime_snapshot(self) -> dict[str, Any]:
        snapshot = dict(self._last_runtime)
        snapshot["chain"] = [
            self._describe_client("primary", self.primary),
            self._describe_client("fallback", self.fallback),
            self._describe_client("local", self.local),
            self._describe_client("local_coder", getattr(self, "local_coder", None)),
            {"stage": "cache", "configured": True, "model": "cache", "base_url": str(self.cache_path)},
        ]
        return snapshot

    # ── 路由 ──

    ROUTE_PROVIDERS = {
        "primary": "primary",
        "fallback": "fallback",
        "local": "local",
        "local_coder": "local_coder",
    }

    def _ensure_dynamic_clients(self, settings_repository=None) -> dict[str, Any]:
        """懒加载动态客户端缓存。

        ★ P0 修复：仅当 settings_repository 不为 None 且尚未设置时才存储，
        避免首次调用时传入 None 导致后续永远无法加载 provider 配置。
        """
        with self._dynamic_clients_lock:
            if self._dynamic_clients is None:
                self._dynamic_clients = {}
            if settings_repository is not None and self._settings_for_providers is None:
                self._settings_for_providers = settings_repository
            return self._dynamic_clients

    def get_client_by_provider(self, provider_name: str, model_name: str | None = None, settings_repository=None) -> OpenAICompatibleClient | None:
        """根据 provider 名称获取对应的 OpenAICompatibleClient。

        先从缓存查找，未命中则从 provider_configs 创建。
        内置 stage（primary/fallback/local）且模型匹配时直接返回现有客户端。
        """
        # 内置 stage 且不指定模型时直接返回
        if provider_name in self.ROUTE_PROVIDERS and not model_name:
            stage = self.ROUTE_PROVIDERS[provider_name]
            return getattr(self, stage, None)

        # 动态 provider 客户端缓存
        cache = self._ensure_dynamic_clients(settings_repository)
        cache_key = f"{provider_name}:{model_name or ''}"
        with self._dynamic_clients_lock:
            if cache_key in cache:
                return cache[cache_key]

        # 加载 provider_configs
        try:
            from core.provider_config import load_providers

            repo = settings_repository or getattr(self, "_settings_for_providers", None)
            if repo is None:
                return None
            providers = load_providers(repo)
            cfg = next((p for p in providers if p.name == provider_name and p.enabled), None)
            if cfg is None:
                return None

            from .base import OpenAICompatibleClient

            resolved_model = model_name or cfg.model
            if not resolved_model and cfg.get_all_models():
                resolved_model = cfg.get_all_models()[0]
            resolved_model = resolved_model or "gpt-4o"

            client = OpenAICompatibleClient(
                api_key=cfg.api_key,
                api_base=cfg.api_base,
                model=resolved_model,
                timeout=cfg.timeout or 300,
            )
            with self._dynamic_clients_lock:
                cache[cache_key] = client
            return client
        except Exception as exc:
            logger.warning("get_client_by_provider(%s) failed: %s", provider_name, exc)
            return None

    def route_chat(
        self,
        provider: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = 900,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """路由到指定 provider，如果目标不可用则沿链回退。

        Provider 必须为 "primary" / "fallback" / "local"。
        """
        if provider not in self.ROUTE_PROVIDERS:
            raise AIServiceError(f"Unknown provider: {provider}", error_kind="invalid_provider")

        chain = self._build_provider_chain(provider)
        attempted_stages: list[str] = []
        last_status = None
        last_transient = False

        for stage_name, client in chain:
            if client is None:
                # 2026-10-15（可观测性）：腿未配置原本静默跳过——排查"全链耗尽"
                # 时看不到任何腿信息（实测 backfill 107 失败无腿日志）。
                logger.warning("route_chat %s client 未配置（None），跳过", stage_name)
                continue
            # B2: 跳过已被健康监控标记为降级的 provider
            if not self._is_stage_available(stage_name):
                logger.warning(
                    "Health monitor: route_chat 跳过已降级 stage %s (provider=%s)",
                    stage_name,
                    self._stage_provider_names.get(stage_name, "?"),
                )
                continue
            attempted_stages.append(stage_name)
            # Budget check before attempting
            if self.budget_manager is not None and not self.budget_manager.check_budget(estimated_api_calls=1):
                logger.info("Budget check failed for route_chat %s, skipping", stage_name)
                continue
            # Circuit breaker check: skip if provider is in open state
            cb_registry = get_circuit_breaker_registry()
            if not cb_registry.is_call_permitted(stage_name):
                logger.info("Circuit breaker OPEN for %s, skipping", stage_name)
                continue
            try:
                resp = client.chat_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                )
                # Record success in circuit breaker (sync version)
                cb_registry.record_success_sync(stage_name)
                self._record_ai_cost(stage_name, client, task_type="route_chat")
                usage = resp.get("usage", {}) or {}
                self._emit_receipt(
                    stage_name,
                    client,
                    status="completed",
                    task_type="route_chat",
                    tokens_input=usage.get("prompt_tokens", 0) or 0,
                    tokens_output=usage.get("completion_tokens", 0) or 0,
                    messages=messages,
                )
                self._record_runtime(
                    stage=f"routed:{stage_name}",
                    degraded=stage_name != provider,
                    cache_hit=False,
                    last_status=last_status,
                    last_model=getattr(client, "model", None),
                    attempted_stages=attempted_stages,
                )
                return resp
            except AIServiceError as exc:
                last_transient = last_transient or getattr(exc, "transient", False)
                last_status = getattr(exc, "status", None) or last_status
                # Record failure in circuit breaker (sync version)
                cb_registry.record_failure_sync(stage_name, exc)
                logger.warning("route_chat %s failed, trying next: %s", stage_name, exc)
                self._record_runtime(
                    stage=f"routed:{stage_name}",
                    degraded=True,
                    cache_hit=False,
                    last_error=str(exc),
                    last_status=last_status,
                    last_model=getattr(client, "model", None),
                    attempted_stages=attempted_stages,
                )
            except Exception as exc:
                # Record failure in circuit breaker (sync version)
                cb_registry.record_failure_sync(stage_name, exc)
                logger.warning("route_chat %s exception, trying next: %s", stage_name, exc)
                self._record_runtime(
                    stage=f"routed:{stage_name}",
                    degraded=True,
                    cache_hit=False,
                    last_error=str(exc),
                    last_status=last_status,
                    last_model=getattr(client, "model", None),
                    attempted_stages=attempted_stages,
                )

        raise AIServiceError(
            f"route_chat: all providers exhausted (requested={provider})",
            transient=bool(last_transient),
            status=last_status,
            error_kind="routing_exhausted",
        )

    def _build_provider_chain(self, preferred: str) -> list[tuple[str, Any]]:
        # [local-leg-policy 2026-09-23] 当前策略：本地模型能力不足，local/local_coder 腿在默认链序中排于云端之后、仅作兑底（审计波次②裁决③：代码保留）。
        # 待本地模型能力达标后，规划让本地模型加入 judge 并接管本地管理。
        if getattr(self, "_local_first", False):
            # local_first：本地腿先行、云端兜底（默认链序 primary→fallback→local 的镜像）
            order = ["local", "local_coder", "primary", "fallback"]
        else:
            order = ["primary", "fallback", "local", "local_coder"]
        if preferred in order:
            order.remove(preferred)
            order.insert(0, preferred)
        return [(name, getattr(self, name)) for name in order]

    # ── 缓存（委托给 AiResponseCache） ──

    def _load_cache_sync(self) -> dict:
        return self._cache_obj._load_cache_sync()

    async def _load_cache(self) -> dict:
        return await self._cache_obj._load_cache()

    async def _prune_cache(self) -> None:
        await self._cache_obj.prune()

    async def _save_cache(self) -> None:
        # 修复 B4: 原实现误调 prune()（清理操作），应调用 save()（保存操作）
        await self._cache_obj.save()

    def _messages_key(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list | None = None,
        tool_choice: str | dict | None = None,
        model: str | None = None,
        stream: bool | None = None,
    ) -> str:
        return self._cache_obj.make_key(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            # cache key 里 tool_choice 仅作哈希标识：dict 形（OpenAI tool_choice）
            # 归一化为 str，避免与 make_key 的 str|None 契约冲突
            tool_choice=str(tool_choice) if tool_choice is not None else None,
            # model/stream 可选透传：参与哈希，隔离不同模型与流式/非流式缓存
            model=model,
            stream=stream,
        )

    async def _get_cached(self, key: str) -> Any:
        return await self._cache_obj.get(key)

    def _get_cached_sync(self, key: str) -> Any:
        return self._cache_obj.get_sync(key)

    def _store_cached_sync(self, key: str, text: str) -> None:
        self._cache_obj.set_sync(key, text)

    def _save_cache_sync(self) -> None:
        self._cache_obj._save_cache_sync()

    # ── 核心接口 ──

    def test_connection(self) -> dict[str, str]:
        clients = [self.primary]
        if self.fallback is not None:
            clients.append(self.fallback)
        if self.local is not None:
            clients.append(self.local)
        lc = self.local_coder
        if lc is not None:
            clients.append(lc)
        last_exc: Exception | None = None
        for client in clients:
            if not hasattr(client, "test_connection"):
                continue
            try:
                return client.test_connection()
            except Exception as exc:
                last_exc = exc
                logger.info("test_connection failed for %s", getattr(client, "__class__", None), exc_info=True)
        raise AIServiceError("所有 AI 提供方连接测试失败", transient=True) from last_exc

    async def atest_connection(self) -> dict[str, str]:
        """异步测试连接 —— 委托给同步实现并在独立线程中执行，避免阻塞事件循环。"""
        return await asyncio.to_thread(self.test_connection)

    @staticmethod
    def _extract_text_from_response(resp: dict[str, Any]) -> str:
        try:
            return str(resp["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:
            raise AIServiceError("回退后未能获取文本内容") from exc

    def _check_privacy_egress(self, messages: list[dict[str, str]]) -> str:
        """检查消息中是否包含仅本地数据，返回隐私级别。"""
        # 云化模式：用户已同意数据上云，隐私守卫永远放行云端。
        if self._cloud_only:
            return "shareable"
        if self._privacy_filter is None:
            return "shareable"
        text = " ".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str))
        if not text.strip():
            return "shareable"
        try:
            return self._privacy_filter(text)
        except Exception:
            # P1-11: 日志级别从 debug 提升到 warning —— privacy_filter 失败会导致
            # 所有请求降级到 local_only（性能下降），生产环境应可见。
            logger.warning("privacy_filter check failed, defaulting to local_only", exc_info=True)
            return "local_only"

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = 900,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        key = self._messages_key(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            # 缓存键加入模型标识与流式标记：非流式 chat_completion 以 primary 模型
            # 作为本次请求的模型标识，避免不同模型/是否流式复用同一缓存键命中错误结果。
            model=getattr(self.primary, "model", None),
            stream=False,
        )

        # P1-3: 提取 per-call timeout（Judge 路由决策使用短超时），透传给底层 client
        timeout = kwargs.pop("timeout", None)
        # E3: JSON 稳健模式——透传 response_format 给具体端点（端点自行判断是否支持）
        response_format = kwargs.pop("response_format", None)

        # S1+S2: 为本次调用生成 correlation_id，用于关联回退链中各阶段
        correlation_id = self._new_correlation_id()

        last_transient = False
        last_status = None
        last_retry_after_seconds = None

        # Privacy egress check: 检测到仅本地数据时，跳过云端（primary/fallback）
        privacy_level = self._check_privacy_egress(messages)
        skip_cloud = privacy_level == "local_only"

        attempted_stages: list[str] = []
        # local_first（默认 False=零行为变化）：开启时本地腿先行、云端兜底
        if getattr(self, "_local_first", False):
            stages = (
                ("local", self.local, "local_openai_compatible"),
                ("local_coder", self.local_coder, "local_openai_compatible"),
                ("primary", self.primary, "openai_compatible"),
                ("fallback", self.fallback, "fallback_openai_compatible"),
            )
        else:
            stages = (
                ("primary", self.primary, "openai_compatible"),
                ("fallback", self.fallback, "fallback_openai_compatible"),
                ("local", self.local, "local_openai_compatible"),
                ("local_coder", self.local_coder, "local_openai_compatible"),
            )
        for stage_name, client, provider_name in stages:
            if skip_cloud and stage_name in ("primary", "fallback"):
                logger.info("Privacy egress guard: 跳过 %s（仅本地数据）", stage_name)
                attempted_stages.append(f"{stage_name}_skipped")
                continue
            if client is None:
                # 2026-10-15（可观测性）：腿未配置原本静默跳过——排查"全链耗尽"
                # 时看不到任何腿信息（实测 backfill 107 失败无腿日志）。
                logger.warning("route_chat %s client 未配置（None），跳过", stage_name)
                continue
            # B2: 跳过已被健康监控标记为降级的 provider
            if not self._is_stage_available(stage_name):
                logger.warning(
                    "Health monitor: 跳过已降级 stage %s (provider=%s)，尝试下一阶段",
                    stage_name,
                    self._stage_provider_names.get(stage_name, "?"),
                )
                attempted_stages.append(f"{stage_name}_degraded")
                continue
            # P1-fix: 断路器检查（与 route_chat 对齐，_cb_registry 可选注入）
            cb = getattr(self, "_cb_registry", None)
            if cb is not None and not cb.is_call_permitted(stage_name):
                logger.info("Circuit breaker open for %s, skip", stage_name)
                attempted_stages.append(f"{stage_name}_circuit_open")
                continue
            attempted_stages.append(stage_name)
            # Budget check before attempting
            if self.budget_manager is not None and not self.budget_manager.check_budget(estimated_api_calls=1):
                logger.info("Budget check failed for %s, skipping to next stage", stage_name)
                continue
            call_start = time.monotonic()
            try:
                resp = client.chat_completion(
                    messages=messages,
                    temperature=temperature,
                    tools=tools,
                    tool_choice=tool_choice,
                    **({"max_tokens": max_tokens} if max_tokens is not None else {}),
                    **({"timeout": timeout} if timeout is not None else {}),
                    **({"response_format": response_format} if response_format is not None else {}),
                )
                try:
                    text = resp["choices"][0]["message"]["content"]
                    if isinstance(text, str) and text.strip():
                        self._store_cached_sync(key, text.strip())
                except Exception:
                    logger.info("%s 响应存储到缓存失败", stage_name, exc_info=True)
                self._record_ai_cost(stage_name, client)
                usage = resp.get("usage", {}) or {}
                self._emit_receipt(
                    stage_name,
                    client,
                    status="completed",
                    task_type="chat_completion",
                    tokens_input=usage.get("prompt_tokens", 0) or 0,
                    tokens_output=usage.get("completion_tokens", 0) or 0,
                    messages=messages,
                )
                # S1+S2: 记录成功链路
                self._trace_call(
                    correlation_id=correlation_id,
                    provider_name=getattr(client, "model", stage_name) or stage_name,
                    stage=stage_name,
                    success=True,
                    latency_ms=int((time.monotonic() - call_start) * 1000),
                    token_count=(usage.get("prompt_tokens", 0) or 0) + (usage.get("completion_tokens", 0) or 0),
                )
                self._record_runtime(
                    stage=stage_name,
                    degraded=stage_name != "primary",
                    cache_hit=False,
                    last_status=last_status,
                    last_model=getattr(client, "model", None),
                    attempted_stages=attempted_stages,
                )
                return resp
            except AIServiceError as exc:
                msg = str(exc)
                last_transient = last_transient or getattr(exc, "transient", False)
                last_status = getattr(exc, "status", None) or last_status
                if last_retry_after_seconds is None:
                    last_retry_after_seconds = getattr(exc, "retry_after_seconds", None)
                tags = build_observability_tags(
                    provider=provider_name,
                    model=getattr(client, "model", None),
                    status=getattr(exc, "status", None),
                    transient=getattr(exc, "transient", False),
                    retry_after_seconds=getattr(exc, "retry_after_seconds", None),
                    fallback_stage=stage_name,
                    cache_hit=False,
                    error_kind=getattr(exc, "error_kind", "unknown_error"),
                )
                logger.warning("%s AI 服务失败，继续回退: tags=%s error=%s", stage_name, tags, msg)
                # S1+S2: 记录失败链路
                self._trace_call(
                    correlation_id=correlation_id,
                    provider_name=getattr(client, "model", stage_name) or stage_name,
                    stage=stage_name,
                    success=False,
                    latency_ms=int((time.monotonic() - call_start) * 1000),
                    error=msg,
                )
                self._record_runtime(
                    stage=stage_name,
                    degraded=stage_name != "primary",
                    cache_hit=False,
                    last_error=msg,
                    last_status=getattr(exc, "status", None),
                    last_model=getattr(client, "model", None),
                    attempted_stages=attempted_stages,
                )
            except Exception as exc:
                logger.warning("%s AI 服务异常，继续回退: %s", stage_name, exc)
                # S1+S2: 记录异常链路
                self._trace_call(
                    correlation_id=correlation_id,
                    provider_name=getattr(client, "model", stage_name) or stage_name,
                    stage=stage_name,
                    success=False,
                    latency_ms=int((time.monotonic() - call_start) * 1000),
                    error=str(exc),
                )
                self._record_runtime(
                    stage=stage_name,
                    degraded=stage_name != "primary",
                    cache_hit=False,
                    last_error=str(exc),
                    last_status=last_status,
                    last_model=getattr(client, "model", None),
                    attempted_stages=attempted_stages,
                )

        # 缓存回退：使用同步版本
        cached = self._get_cached_sync(key)
        if cached:
            cached_text = cached.get("text", cached) if isinstance(cached, dict) else cached
            # S1+S2: 记录缓存命中
            self._trace_call(
                correlation_id=correlation_id,
                provider_name="cache",
                stage="cache",
                success=True,
            )
            self._record_runtime(
                stage="cache",
                degraded=True,
                cache_hit=True,
                last_status=last_status,
                last_model="cache",
                attempted_stages=attempted_stages,
            )
            logger.info(
                "使用 AI 缓存作为回退响应: tags=%s",
                build_observability_tags(
                    provider="cache",
                    model=getattr(self.primary, "model", None),
                    status=last_status,
                    transient=last_transient,
                    retry_after_seconds=last_retry_after_seconds,
                    fallback_stage="cache",
                    cache_hit=True,
                    error_kind="cache_hit",
                ),
            )
            return {"choices": [{"message": {"content": cached_text}}]}

        logger.warning(
            "AI 回退链路失败: tags=%s key=%s",
            build_observability_tags(
                provider="fallback_chain",
                model=getattr(self.primary, "model", None),
                status=last_status,
                transient=bool(last_transient),
                retry_after_seconds=last_retry_after_seconds,
                fallback_stage="cache",
                cache_hit=False,
                error_kind="fallback_exhausted",
            ),
            key[:12],
        )
        # S1+S2: 记录链路耗尽
        self._trace_call(
            correlation_id=correlation_id,
            provider_name="fallback_chain",
            stage="exhausted",
            success=False,
            error="all stages exhausted and cache miss",
        )
        self._record_runtime(
            stage="failed",
            degraded=True,
            cache_hit=False,
            last_error="AI 服务不可用，且未命中本地回退",
            last_status=last_status,
            last_model=getattr(self.primary, "model", None),
            attempted_stages=attempted_stages,
        )
        raise AIServiceError(
            "AI 服务不可用，且未命中本地回退",
            transient=bool(last_transient),
            status=last_status,
            retry_after_seconds=last_retry_after_seconds,
            error_kind="fallback_exhausted",
        )

    async def achat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = 900,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """异步 chat completion —— 委托给同步实现并在独立线程中执行，避免阻塞事件循环。

        底层 HTTP 调用现在通过 base.py 的 httpx 同步客户端完成；如需原生 httpx.AsyncClient，
        可直接使用 ``OpenAICompatibleClient.achat_completion``。
        """
        return await asyncio.to_thread(
            self.chat_completion,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs,
        )

    def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = 900,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ):
        """流式 chat completion — 代理到主服务或本地 LLM。

        调用后可检查 self._last_stream_tool_calls 获取累积的 tool_call 信息。
        """
        _last_stream_tool_calls_var.set([])
        key = self._messages_key(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            # 流式路径以 stream=True 参与哈希，与上文非流式 chat_completion 的
            # 缓存隔离，避免流式/非流式复用同一缓存键命中错误文本。
            model=getattr(self.primary, "model", None),
            stream=True,
        )

        # Privacy egress check: 检测到仅本地数据时，跳过云端（primary/fallback）
        privacy_level = self._check_privacy_egress(messages)
        skip_cloud = privacy_level == "local_only"

        clients = []
        if getattr(self, "_local_first", False) and not skip_cloud:
            # local_first：本地腿先行，云端兜底（顺序仅影响尝试次序，语义不变）
            if self.local is not None:
                clients.append(self.local)
            if self.local_coder is not None:
                clients.append(self.local_coder)
            if self.primary is not None:
                clients.append(self.primary)
            if self.fallback is not None:
                clients.append(self.fallback)
        else:
            if not skip_cloud:
                if self.primary is not None:
                    clients.append(self.primary)
                if self.fallback is not None:
                    clients.append(self.fallback)
            if self.local is not None:
                clients.append(self.local)
            if self.local_coder is not None:
                clients.append(self.local_coder)

        logger.info(
            "chat_completion_stream clients: count=%d local=%s local_coder=%s",
            len(clients),
            "model=" + str(getattr(self.local, "model", None)) if self.local else "None",
            "model=" + str(getattr(self.local_coder, "model", None)) if self.local_coder else "None",
        )

        def _stage_name(client_obj) -> str:
            if client_obj is self.local_coder:
                return "local_coder"
            return "local" if client_obj is self.local else ("fallback" if client_obj is self.fallback else "primary")

        last_exc: Exception | None = None
        for client in clients:
            if not hasattr(client, "chat_completion_stream"):
                continue
            stream_stage = _stage_name(client)
            # Budget check before attempting
            if self.budget_manager is not None and not self.budget_manager.check_budget(estimated_api_calls=1):
                logger.info("Budget check failed for stream %s, skipping", stream_stage)
                continue
            buffer: list[str] = []
            try:
                _stream_kwargs: dict[str, Any] = dict(messages=messages, temperature=temperature, tools=tools, tool_choice=tool_choice)
                if max_tokens is not None:
                    _stream_kwargs["max_tokens"] = max_tokens
                for chunk in client.chat_completion_stream(**_stream_kwargs):
                    buffer.append(chunk)
                    yield chunk
                # Propagate accumulated tool_calls from the underlying client
                if hasattr(client, "_last_stream_tool_calls") and client._last_stream_tool_calls:
                    _last_stream_tool_calls_var.set(list(client._last_stream_tool_calls))
                if buffer:
                    self._store_cached_sync(key, "".join(buffer).strip())
                self._record_ai_cost(stream_stage, client, task_type="chat_completion_stream")
                self._emit_receipt(stream_stage, client, status="completed", task_type="chat_completion_stream", messages=messages)
                return
            except Exception as exc:
                last_exc = exc
                logger.warning("chat_completion_stream failed for %s: %s", getattr(client, "__class__", None), exc)
                # P0-3 修复：只在 stream 从未 yield 过任何 chunk 时（buffer 空）
                # 才允许回退到同 client 的非流式调用。
                # 历史 bug：buffer 已有内容时再调一次完整 chat_completion，
                # 会导致：
                #   1. 重复扣费（同一次提问被计费两次）
                #   2. UI 重复展示（前半段流式 + 完整文本拼在一起）
                # buffer 已有内容意味着用户已经看到部分回复 — 不再尝试任何
                # 回退，让外层 fallback for-loop 切到下一个 client。
                # P1-fix: 流式失败一律切下一个 client，不再对同 client 非流式重试
                # （即使 buffer 为空）。同一 provider 的流式失败意味着非流式大概率也失败，
                # 且重试会产生重复计费。
                logger.info(
                    "stream %s 失败（buffer=%d chunks），切下一个 client",
                    stream_stage,
                    len(buffer),
                )
                continue

        # 所有流式 client 均失败 → 检查缓存
        cached = self._get_cached_sync(key)
        if cached:
            cached_text = cached.get("text") if isinstance(cached, dict) else cached
            if isinstance(cached_text, str) and cached_text.strip():
                yield cached_text.strip()
                return

        # P1-fix: 不再走完整回退链 self.chat_completion()（重复计费）
        raise AIServiceError(
            "流式对话在所有提供方均失败",
            transient=bool(getattr(last_exc, "transient", False)),
        ) from last_exc

    async def achat_completion_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = 900,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """异步流式 chat completion —— 在后台线程运行同步生成器，避免阻塞事件循环。"""
        return _async_iter_from_sync(
            lambda: self.chat_completion_stream(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
            )
        )

    def stream_chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ):
        """流式 + 工具调用（yield 结构化事件 dict）。

        与 chat_completion_stream（yield 纯文本）不同，此方法 yield 事件 dict：
        - {"type": "text", "data": "..."}
        - {"type": "tool_call", "data": {...}}       (增量)
        - {"type": "tool_call_done", "data": {...}}  (完整)
        - {"type": "done"}

        遍历 primary → fallback → local 客户端，首个成功的胜出。
        仅在连接阶段重试，开始传输后不再回退（避免重复计费）。
        """
        # Privacy egress check
        privacy_level = self._check_privacy_egress(messages)
        skip_cloud = privacy_level == "local_only"

        clients: list = []
        if getattr(self, "_local_first", False) and not skip_cloud:
            # local_first：本地腿先行，云端兜底（顺序仅影响尝试次序，语义不变）
            if self.local is not None:
                clients.append(self.local)
            if self.local_coder is not None:
                clients.append(self.local_coder)
            if self.primary is not None:
                clients.append(self.primary)
            if self.fallback is not None:
                clients.append(self.fallback)
        else:
            if not skip_cloud:
                if self.primary is not None:
                    clients.append(self.primary)
                if self.fallback is not None:
                    clients.append(self.fallback)
            if self.local is not None:
                clients.append(self.local)
            if self.local_coder is not None:
                clients.append(self.local_coder)

        def _stage_name(client_obj) -> str:
            if client_obj is self.local_coder:
                return "local_coder"
            return "local" if client_obj is self.local else ("fallback" if client_obj is self.fallback else "primary")

        last_exc: Exception | None = None
        for client in clients:
            stream_fn = getattr(client, "stream_chat_with_tools", None)
            if stream_fn is None:
                # 客户端不支持 stream_chat_with_tools，跳过
                continue
            stream_stage = _stage_name(client)
            # Budget check
            if self.budget_manager is not None and not self.budget_manager.check_budget(estimated_api_calls=1):
                logger.info("Budget check failed for stream_with_tools %s, skipping", stream_stage)
                continue
            has_yielded = False
            try:
                for event in stream_fn(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    has_yielded = True
                    yield event
                self._record_ai_cost(stream_stage, client, task_type="stream_chat_with_tools")
                self._emit_receipt(
                    stream_stage,
                    client,
                    status="completed",
                    task_type="stream_chat_with_tools",
                    messages=messages,
                )
                # B5（2026-08-26）：成功终止路径补发 done 事件，闭合事件流契约
                # （docstring 承诺 yield {"type": "done"}）；调用方据此结束流。
                yield {"type": "done"}
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "stream_chat_with_tools failed for %s: %s (has_yielded=%s)",
                    getattr(client, "__class__", None),
                    exc,
                    has_yielded,
                )
                if has_yielded:
                    # 已开始传输，不再回退避免重复计费
                    # 推送一个 error 事件让调用方知道流中断
                    yield {"type": "error", "data": str(exc)}
                    return
                continue

        # 所有客户端都失败
        yield {"type": "error", "data": f"流式+工具调用在所有提供方均失败: {last_exc}" if last_exc else "无可用 AI 提供方"}

    async def astream_chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """异步流式 + 工具调用 —— 在后台线程运行同步生成器，避免阻塞事件循环。"""
        return _async_iter_from_sync(
            lambda: self.stream_chat_with_tools(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )

    def complete_text(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = 900,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
    ) -> str:
        # 修复 B5: 原实现将 preferred_provider/preferred_model 传给 chat_completion，
        # 但 FallbackOpenAIClient.chat_completion 不支持这两个参数（被 **kwargs 静默忽略）。
        # 改为通过 get_client_by_provider 显式路由。
        if preferred_provider:
            client = self.get_client_by_provider(preferred_provider, preferred_model)
            if client is not None:
                _cc_kwargs: dict[str, Any] = {}
                if max_tokens is not None:
                    _cc_kwargs["max_tokens"] = max_tokens
                resp = client.chat_completion(messages, temperature=temperature, **_cc_kwargs)
                return self._extract_text_from_response(resp)
            logger.warning("complete_text: preferred_provider %s not found", preferred_provider)
        resp = self.chat_completion(messages=messages, temperature=temperature, max_tokens=max_tokens)
        return self._extract_text_from_response(resp)

    async def acomplete_text(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = 900,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
    ) -> str:
        """异步 complete_text —— 委托给同步实现并在独立线程中执行，避免阻塞事件循环。"""
        return await asyncio.to_thread(
            self.complete_text,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
        )

    # ── 视觉能力 ──

    def chat_completion_vision(
        self,
        text: str,
        image_base64: str,
        image_format: str = "png",
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = 900,
    ) -> dict[str, Any]:
        """带图片的 chat completion — 优先路由到 purpose=vision 的专用 provider，
        未配置时回退到 primary（支持 vision 的云端模型）。"""
        if self.budget_manager is not None and not self.budget_manager.check_budget(estimated_api_calls=1):
            raise AIServiceError("预算不足，无法执行视觉分析")

        # 候选链：专用 vision provider → primary
        candidates: list[tuple[str, OpenAICompatibleClient]] = []
        if self.vision_client is not None and hasattr(self.vision_client, "chat_completion_vision"):
            candidates.append(("vision", self.vision_client))
        if hasattr(self.primary, "chat_completion_vision"):
            candidates.append(("primary", self.primary))
        if not candidates:
            raise AIServiceError("未配置支持视觉能力的 AI provider（可在 provider 中添加 purpose=vision 的视觉模型）")

        last_exc: Exception | None = None
        for stage, client in candidates:
            try:
                resp = client.chat_completion_vision(
                    text=text,
                    image_base64=image_base64,
                    image_format=image_format,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self._record_ai_cost(stage, client, task_type="vision")
                usage = resp.get("usage", {}) or {}
                self._emit_receipt(
                    stage,
                    client,
                    status="completed",
                    task_type="vision",
                    tokens_input=usage.get("prompt_tokens", 0) or 0,
                    tokens_output=usage.get("completion_tokens", 0) or 0,
                )
                return resp
            except AIServiceError as exc:
                last_exc = exc
                logger.warning("vision 调用失败(%s)，尝试下一个候选: %s", stage, exc)
            except Exception as exc:
                last_exc = exc
                logger.warning("vision 调用异常(%s)，尝试下一个候选: %s", stage, exc)
        raise AIServiceError(f"视觉分析失败: {last_exc}") from last_exc

    def analyze_image(
        self,
        image_base64: str,
        image_format: str = "png",
        prompt: str = "请详细描述这张图片的内容。",
        max_tokens: int = 500,
    ) -> str:
        """分析图片内容并返回文字描述。"""
        resp = self.chat_completion_vision(
            text=prompt,
            image_base64=image_base64,
            image_format=image_format,
            system_prompt="你是一个图片分析助手。请用中文详细描述图片中的内容。",
            max_tokens=max_tokens,
        )
        return self._extract_text_from_response(resp)


# JudgeAwareClient 现位于同目录 _judge_aware_client.py（旧版本已于 P2-8 清理）
