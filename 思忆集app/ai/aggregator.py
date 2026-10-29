# -*- coding: utf-8 -*-
"""API 聚合器核心。

在现有 FallbackOpenAIClient 之上提供多 provider 并行调用与结果聚合。
支持策略：race / vote / best_of_n / cost_first / merge。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any, AsyncIterator

from ai.aggregation_strategies import (
    CandidateResult,
    best_of_n_strategy,
    cost_first_strategy,
    merge_strategy,
    race_strategy,
    vote_strategy,
)
from ai.aggregation_telemetry import AggregationTelemetry
from ai.provider_quota import ProviderQuota

logger = logging.getLogger("siyi.ai.aggregator")


class AggregationStrategy(StrEnum):
    RACE = auto()
    VOTE = auto()
    BEST_OF_N = auto()
    COST_FIRST = auto()
    MERGE = auto()


@dataclass
class ProviderTier:
    """候选 provider 描述。"""

    provider_name: str
    model: str
    tier: str = "cloud"
    cost_per_1k_tokens: float = 0.0
    priority: int = 0
    timeout_ms: int = 30000
    extra: dict[str, Any] | None = None


@dataclass
class AggregationResult:
    """聚合最终结果。"""

    strategy: str
    winner: str
    content: str
    latency_ms: int
    cost_estimate: float
    attempted: list[str]
    failed: list[str]
    all_results: list[CandidateResult]


class ApiAggregator:
    """多 provider 并行调用 + 结果聚合器。"""

    def __init__(
        self,
        *,
        client_factory: Any,
        providers: list[ProviderTier] | None = None,
        quota_manager: ProviderQuota | None = None,
        telemetry: AggregationTelemetry | None = None,
        default_strategy: AggregationStrategy = AggregationStrategy.RACE,
        default_timeout_ms: int = 30000,
        max_concurrent: int = 5,
        cost_per_1k_default: float = 0.5,
    ) -> None:
        """初始化。

        Args:
            client_factory: 可调用对象，签名 (provider_name: str, model: str) -> OpenAICompatibleClient | None。
                通常传入 FallbackOpenAIClient.get_client_by_provider。
            providers: 候选 provider 列表。为空时只走 default 策略的本地 provider。
            quota_manager: ProviderQuota 实例。
            default_strategy: 默认聚合策略。
            default_timeout_ms: 默认整体超时。
            max_concurrent: 最大并发 provider 数。
            cost_per_1k_default: 默认每千 token 成本估算（美元）。
        """
        self._client_factory = client_factory
        self._providers = list(providers) if providers else []
        self._quota = quota_manager or ProviderQuota()
        self._telemetry = telemetry
        self._default_strategy = default_strategy
        self._default_timeout_ms = default_timeout_ms
        self._max_concurrent = max_concurrent
        self._cost_per_1k_default = cost_per_1k_default
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def set_provider_config(
        self,
        provider_name: str,
        model: str,
        *,
        daily_token_limit: int | None = None,
        daily_cost_limit: float | None = None,
        rate_limit_rpm: int | None = None,
    ) -> None:
        """快捷设置 provider 配额配置。"""
        from ai.provider_quota import QuotaConfig

        self._quota.set_config(
            provider_name,
            QuotaConfig(
                daily_token_limit=daily_token_limit,
                daily_cost_limit=daily_cost_limit,
                rate_limit_rpm=rate_limit_rpm,
            ),
        )

    async def aggregate(
        self,
        messages: list[dict[str, str]],
        *,
        strategy: AggregationStrategy | str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout_ms: int | None = None,
        min_providers: int = 1,
        max_providers: int | None = None,
        **kwargs: Any,
    ) -> AggregationResult:
        """执行聚合调用。

        流式请求当前不支持聚合，会直接返回第一个可用 provider 的结果。
        """
        if strategy is None:
            strategy = self._default_strategy
        if isinstance(strategy, str):
            strategy = AggregationStrategy(strategy)
        timeout_ms = timeout_ms or self._default_timeout_ms
        providers = self._select_providers(strategy, max_providers)

        if strategy == AggregationStrategy.COST_FIRST:
            return await self._run_cost_first(
                providers,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                cheap_timeout_ms=min(2000, timeout_ms // 3),
                **kwargs,
            )
        if strategy == AggregationStrategy.RACE:
            results, winner = await self._race_call(
                providers,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_ms=timeout_ms,
                **kwargs,
            )
        else:
            results = await self._parallel_call(
                providers,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_ms=timeout_ms,
                **kwargs,
            )
            if strategy == AggregationStrategy.VOTE:
                winner = await vote_strategy(results)
            elif strategy == AggregationStrategy.BEST_OF_N:
                original_question = (
                    "\n".join(
                        str(m.get("content", ""))
                        for m in messages
                        if m.get("role") == "user"
                    )
                    or str(messages[-1].get("content", ""))
                )
                winner = await best_of_n_strategy(results, original_question, self._judge_call)
            elif strategy == AggregationStrategy.MERGE:
                original_question = (
                    "\n".join(
                        str(m.get("content", ""))
                        for m in messages
                        if m.get("role") == "user"
                    )
                    or str(messages[-1].get("content", ""))
                )
                winner = await merge_strategy(results, original_question, self._judge_call)
            else:
                winner = await race_strategy(results)

        result = AggregationResult(
            strategy=str(strategy.value),
            winner=winner.provider_name,
            content=winner.content,
            latency_ms=winner.latency_ms,
            cost_estimate=self._estimate_total_cost(results),
            attempted=[r.provider_name for r in results],
            failed=[r.provider_name for r in results if not r.success],
            all_results=results,
        )
        if self._telemetry is not None:
            try:
                self._telemetry.log(result, content=winner.content)
            except Exception:
                logger.debug("telemetry logging failed", exc_info=True)
        return result

    async def aggregate_stream(
        self,
        messages: list[dict[str, str]],
        *,
        strategy: AggregationStrategy | str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout_ms: int | None = None,
        min_providers: int = 1,
        max_providers: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式聚合调用。

        输出格式：
        - {"type": "chunk", "data": str}
        - {"type": "meta", "strategy": ..., "winner": ..., "latency_ms": ..., ...}

        支持策略：
        - race: 竞速，取第一个产出非空 chunk 的 provider 继续流式输出
        - cost_first: 先尝试最便宜的 provider 流式输出，低质量/超时则升舱
        - vote / best_of_n / merge: 先并行收集完整回复，再一次性输出聚合结果
        """
        if strategy is None:
            strategy = self._default_strategy
        if isinstance(strategy, str):
            strategy = AggregationStrategy(strategy)
        timeout_ms = timeout_ms or self._default_timeout_ms
        providers = self._select_providers(strategy, max_providers)
        start = time.monotonic()
        try:
            if strategy == AggregationStrategy.RACE:
                async for item in self._race_stream(
                    providers,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout_ms=timeout_ms,
                    **kwargs,
                ):
                    yield item
                return
            if strategy == AggregationStrategy.COST_FIRST:
                async for item in self._cost_first_stream(
                    providers,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout_ms=timeout_ms,
                    **kwargs,
                ):
                    yield item
                return
            # vote / best_of_n / merge：先聚合再一次性输出
            result = await self.aggregate(
                messages,
                strategy=strategy,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_ms=timeout_ms,
                max_providers=max_providers,
                **kwargs,
            )
            if result.content:
                yield {"type": "chunk", "data": result.content}
            yield {
                "type": "meta",
                "strategy": result.strategy,
                "winner": result.winner,
                "latency_ms": result.latency_ms,
                "cost_estimate": result.cost_estimate,
                "attempted": result.attempted,
                "failed": result.failed,
            }
        except Exception:
            logger.exception("aggregate_stream failed")
            raise

    # ── 内部实现 ────────────────────────────────────────────

    def _select_providers(
        self, strategy: AggregationStrategy, max_providers: int | None
    ) -> list[ProviderTier]:
        """根据策略选择候选 provider。"""
        providers = list(self._providers)
        if not providers:
            return providers
        # cost_first 按成本升序；其余按优先级升序（同优先级按成本）
        if strategy == AggregationStrategy.COST_FIRST:
            providers.sort(key=lambda p: (p.cost_per_1k_tokens, p.priority))
        else:
            providers.sort(key=lambda p: (p.priority, p.cost_per_1k_tokens))
        limit = max_providers or self._max_concurrent
        return providers[:limit]

    async def _parallel_call(
        self,
        providers: list[ProviderTier],
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout_ms: int,
        **kwargs: Any,
    ) -> list[CandidateResult]:
        """并发调用多个 provider，返回所有结果。"""
        if not providers:
            return []
        tasks = [
            asyncio.create_task(
                self._call_one(
                    p,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout_ms=timeout_ms,
                    **kwargs,
                )
            )
            for p in providers
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[CandidateResult] = []
        for prov, raw in zip(providers, raw_results):
            if isinstance(raw, CandidateResult):
                results.append(raw)
            elif isinstance(raw, BaseException):
                results.append(
                    CandidateResult(
                        provider_name=prov.provider_name,
                        model=prov.model,
                        content="",
                        latency_ms=0,
                        success=False,
                        error=f"{type(raw).__name__}: {raw}",
                    )
                )
            else:
                results.append(
                    CandidateResult(
                        provider_name=prov.provider_name,
                        model=prov.model,
                        content="",
                        latency_ms=0,
                        success=False,
                        error=f"unexpected result type: {type(raw).__name__}",
                    )
                )
        return results

    async def _race_call(
        self,
        providers: list[ProviderTier],
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout_ms: int,
        **kwargs: Any,
    ) -> tuple[list[CandidateResult], CandidateResult]:
        """竞速调用：返回第一个成功结果，同时尽量收集其他结果。"""
        if not providers:
            empty = CandidateResult(
                provider_name="aggregate",
                model="race",
                content="",
                latency_ms=0,
                success=False,
                error="无可用 provider",
            )
            return [], empty
        tasks = {
            asyncio.create_task(
                self._call_one(
                    p,
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout_ms=timeout_ms,
                    **kwargs,
                )
            ): p
            for p in providers
        }
        all_tasks = set(tasks.keys())
        pending = set(all_tasks)
        results: list[CandidateResult] = []
        winner: CandidateResult | None = None
        timeout_s = timeout_ms / 1000.0

        def _result_for_exc(task: asyncio.Task, exc: Exception) -> CandidateResult:
            provider = tasks.get(task)
            provider_name = provider.provider_name if provider else "unknown"
            model = provider.model if provider else "unknown"
            return CandidateResult(
                provider_name=provider_name,
                model=model,
                content="",
                latency_ms=0,
                success=False,
                error=str(exc),
            )

        while pending:
            done, pending = await asyncio.wait(
                pending, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    result = task.result()
                except Exception as exc:
                    result = _result_for_exc(task, exc)
                results.append(result)
                if winner is None and result.success and result.content:
                    winner = result
            if winner is not None:
                break
            if not pending:
                break
        # 尽量收集剩余结果（每个最多再等 0.5s）
        for task in list(pending):
            try:
                result = await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                results.append(result)
                pending.discard(task)
            except asyncio.TimeoutError:
                task.cancel()
            except Exception as exc:
                results.append(_result_for_exc(task, exc))
                pending.discard(task)
        if winner is None:
            winner = CandidateResult(
                provider_name="aggregate",
                model="race",
                content="",
                latency_ms=int(timeout_ms),
                success=False,
                error="所有 provider 均失败或超时",
            )
        return results, winner

    async def _call_one(
        self,
        provider: ProviderTier,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout_ms: int,
        **kwargs: Any,
    ) -> CandidateResult:
        """调用单个 provider。"""
        start = time.monotonic()
        async with self._semaphore:
            # 配额预检：按最大输出 token 估算
            if not self._quota.check(
                provider.provider_name,
                provider.model,
                tokens_input=0,
                tokens_output=max_tokens,
            ):
                return CandidateResult(
                    provider_name=provider.provider_name,
                    model=provider.model,
                    content="",
                    latency_ms=int((time.monotonic() - start) * 1000),
                    success=False,
                    error="quota exceeded",
                )
            client = self._client_factory(provider.provider_name, provider.model)
            if client is None:
                return CandidateResult(
                    provider_name=provider.provider_name,
                    model=provider.model,
                    content="",
                    latency_ms=int((time.monotonic() - start) * 1000),
                    success=False,
                    error="client not available",
                )
            # 同步 client 放到线程池执行，整体用 wait_for 控制超时
            timeout_s = min(timeout_ms, provider.timeout_ms) / 1000.0
            loop = asyncio.get_event_loop()
            try:
                resp = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: client.chat_completion(
                            messages=messages,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            **({"timeout": int(timeout_s)} if timeout_s else {}),
                            **(provider.extra or {}),
                            **kwargs,
                        ),
                    ),
                    timeout=timeout_s,
                )
                content = ""
                if isinstance(resp, dict):
                    try:
                        content = str(resp["choices"][0]["message"]["content"] or "").strip()
                    except Exception:
                        content = str(resp.get("content") or "").strip()
                latency_ms = int((time.monotonic() - start) * 1000)
                usage = (resp.get("usage") or {}) if isinstance(resp, dict) else {}
                tokens_input = usage.get("prompt_tokens", 0) or 0
                tokens_output = usage.get("completion_tokens", 0) or 0
                # 记录配额消耗
                self._quota.consume(
                    provider.provider_name,
                    provider.model,
                    tokens_input=tokens_input,
                    tokens_output=tokens_output,
                )
                return CandidateResult(
                    provider_name=provider.provider_name,
                    model=provider.model,
                    content=content,
                    latency_ms=latency_ms,
                    success=bool(content),
                    tokens_input=tokens_input,
                    tokens_output=tokens_output,
                )
            except asyncio.TimeoutError:
                return CandidateResult(
                    provider_name=provider.provider_name,
                    model=provider.model,
                    content="",
                    latency_ms=int((time.monotonic() - start) * 1000),
                    success=False,
                    error="timeout",
                )
            except Exception as exc:
                return CandidateResult(
                    provider_name=provider.provider_name,
                    model=provider.model,
                    content="",
                    latency_ms=int((time.monotonic() - start) * 1000),
                    success=False,
                    error=str(exc),
                )

    async def _judge_call(self, messages: list[dict[str, str]]) -> str:
        """用于 best_of_n / merge 的内部评判调用。

        使用 providers 列表中最后一个可用 provider 作为 judge（通常是最可靠/最贵的模型）。
        """
        if not self._providers:
            raise RuntimeError("no provider available for judge call")
        # 倒序遍历：优先用最贵/最可靠的 provider 做评判
        for provider in reversed(self._providers):
            client = self._client_factory(provider.provider_name, provider.model)
            if client is None:
                continue
            loop = asyncio.get_event_loop()
            try:
                resp = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: client.chat_completion(
                            messages=messages, temperature=0.3, max_tokens=4096
                        ),
                    ),
                    timeout=30.0,
                )
            except Exception as exc:
                logger.debug("judge provider %s failed: %s", provider.provider_name, exc)
                continue
            if isinstance(resp, dict):
                try:
                    return str(resp["choices"][0]["message"]["content"] or "").strip()
                except Exception:
                    return str(resp.get("content") or "").strip()
            return str(resp).strip()
        raise RuntimeError("all judge providers failed")

    async def _run_cost_first(
        self,
        providers: list[ProviderTier],
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        cheap_timeout_ms: int,
        **kwargs: Any,
    ) -> AggregationResult:
        """执行 cost_first 策略。"""
        # 转为 dict 供 cost_first_strategy 使用（策略层不依赖 ProviderTier）
        provider_dicts = [
            {
                "provider_name": p.provider_name,
                "model": p.model,
                "timeout_ms": p.timeout_ms,
                "extra": p.extra or {},
            }
            for p in providers
        ]

        async def call_fn(p: dict[str, Any], msgs: list[dict[str, str]]) -> CandidateResult:
            # 从 dict 找回对应的 ProviderTier（携带完整配置）
            tier = next(
                (
                    x
                    for x in providers
                    if x.provider_name == p["provider_name"] and x.model == p["model"]
                ),
                None,
            )
            if tier is None:
                return CandidateResult(
                    provider_name=p["provider_name"],
                    model=p["model"],
                    content="",
                    latency_ms=0,
                    success=False,
                    error="tier not found",
                )
            return await self._call_one(
                tier,
                msgs,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_ms=p["timeout_ms"],
                **kwargs,
            )

        start = time.monotonic()
        winner = await cost_first_strategy(
            provider_dicts, messages, call_fn, cheap_timeout_ms=cheap_timeout_ms
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        winner.latency_ms = max(winner.latency_ms, latency_ms)

        result = AggregationResult(
            strategy=AggregationStrategy.COST_FIRST.value,
            winner=winner.provider_name,
            content=winner.content,
            latency_ms=winner.latency_ms,
            cost_estimate=self._estimate_cost(winner),
            attempted=[p.provider_name for p in providers],
            failed=[],
            all_results=[winner],
        )
        if self._telemetry is not None:
            try:
                self._telemetry.log(result, content=winner.content)
            except Exception:
                logger.debug("telemetry logging failed", exc_info=True)
        return result

    def _estimate_cost(self, result: CandidateResult) -> float:
        """估算单个结果的成本。"""
        provider = next(
            (
                p
                for p in self._providers
                if p.provider_name == result.provider_name and p.model == result.model
            ),
            None,
        )
        cost_per_1k = provider.cost_per_1k_tokens if provider else self._cost_per_1k_default
        tokens = result.tokens_input + result.tokens_output
        return tokens * cost_per_1k / 1000

    def _estimate_total_cost(self, results: list[CandidateResult]) -> float:
        return sum(self._estimate_cost(r) for r in results)

    def _is_quality_ok(self, text: str) -> bool:
        """简单质量判断：非空且不包含明显拒绝关键词。

        阈值从 10 降至 1（非空即可），避免短有效回答（"好的"/"OK"/"Done."）
        触发不必要的升舱。拒绝关键词检测保留。
        """
        if not text or not text.strip():
            return False
        refusals = ("无法", "不能", "I can't", "I cannot", "作为AI")
        first_50 = text[:50]
        return not any(r in first_50 for r in refusals)

    async def _stream_one(
        self,
        idx: int,
        provider: ProviderTier,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        queue: asyncio.Queue,
        **kwargs: Any,
    ) -> None:
        """在独立任务中调用单个 provider 的流式接口，逐 chunk 写入 queue。

        使用 call_soon_threadsafe 将每个 chunk 立即投递到 asyncio Queue，
        使 race 策略能真正基于首 chunk 竞速，而非等全部响应完成。
        """
        provider_name = provider.provider_name
        model = provider.model
        # 配额预检
        if not self._quota.check(provider_name, model, tokens_input=0, tokens_output=max_tokens):
            await queue.put((idx, "error", provider_name, "quota exceeded"))
            return
        client = self._client_factory(provider_name, model)
        if client is None:
            await queue.put((idx, "error", provider_name, "client not available"))
            return
        stream_fn = getattr(client, "chat_completion_stream", None)
        if stream_fn is None:
            await queue.put((idx, "error", provider_name, "client not streamable"))
            return
        start = time.monotonic()
        loop = asyncio.get_event_loop()

        def _iter_stream():
            try:
                for chunk in stream_fn(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **(provider.extra or {}),
                    **kwargs,
                ):
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        (idx, "chunk", chunk, int((time.monotonic() - start) * 1000)),
                    )
                loop.call_soon_threadsafe(queue.put_nowait, (idx, "done"))
            except Exception as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait, (idx, "error", provider_name, str(exc))
                )

        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, _iter_stream),
                timeout=provider.timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            await queue.put((idx, "error", provider_name, "timeout"))
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.debug("stream_one %s failed: %s", provider_name, exc)
            await queue.put((idx, "error", provider_name, str(exc)))

    async def _race_stream(
        self,
        providers: list[ProviderTier],
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout_ms: int,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """竞速流式：第一个产出非空 chunk 的 provider 胜出并继续输出。"""
        if not providers:
            yield {"type": "chunk", "data": ""}
            yield {
                "type": "meta",
                "strategy": AggregationStrategy.RACE.value,
                "winner": "aggregate",
                "latency_ms": 0,
                "cost_estimate": 0.0,
                "attempted": [],
                "failed": [],
            }
            return
        queue: asyncio.Queue = asyncio.Queue()
        tasks = [
            asyncio.create_task(
                self._stream_one(i, p, messages, temperature, max_tokens, queue, **kwargs)
            )
            for i, p in enumerate(providers)
        ]
        winner_idx = -1
        winner_name = "aggregate"
        timeout_s = timeout_ms / 1000.0
        deadline = time.monotonic() + timeout_s
        failed: list[str] = []
        attempted = [p.provider_name for p in providers]
        started = time.monotonic()
        full_content = ""
        try:
            # Phase 1: 等首个非空 chunk 定胜负
            while winner_idx == -1:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                idx = item[0]
                kind = item[1]
                if kind == "chunk":
                    chunk = item[2]
                    if chunk:
                        winner_idx = idx
                        winner_name = providers[winner_idx].provider_name
                        full_content += chunk
                        yield {"type": "chunk", "data": chunk}
                        break
                elif kind == "done":
                    continue
                elif kind == "error":
                    failed.append(str(item[2]))
            if winner_idx == -1:
                # 无胜者：输出空内容 + meta 并清理
                yield {"type": "chunk", "data": ""}
                for t in tasks:
                    if not t.done():
                        t.cancel()
                yield {
                    "type": "meta",
                    "strategy": AggregationStrategy.RACE.value,
                    "winner": "aggregate",
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "cost_estimate": 0.0,
                    "attempted": attempted,
                    "failed": failed or attempted,
                }
                for t in tasks:
                    if not t.done():
                        t.cancel()
                        try:
                            await t
                        except (Exception, asyncio.CancelledError):
                            pass
                return
            # Phase 2: 取消其他任务
            for i, t in enumerate(tasks):
                if i != winner_idx and not t.done():
                    t.cancel()
            # Phase 3: 持续消费胜出者的 chunk
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                idx = item[0]
                kind = item[1]
                if idx != winner_idx:
                    continue
                if kind == "chunk":
                    chunk = item[2]
                    full_content += chunk
                    yield {"type": "chunk", "data": chunk}
                elif kind == "done":
                    break
                elif kind == "error":
                    failed.append(str(item[2]))
                    break
            latency_ms = int((time.monotonic() - started) * 1000)
            cost = self._estimate_stream_cost(providers[winner_idx], full_content)
            yield {
                "type": "meta",
                "strategy": AggregationStrategy.RACE.value,
                "winner": winner_name,
                "latency_ms": latency_ms,
                "cost_estimate": cost,
                "attempted": attempted,
                "failed": failed,
            }
            for t in tasks:
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except (Exception, asyncio.CancelledError):
                        pass
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except (Exception, asyncio.CancelledError):
                        pass

    async def _cost_first_stream(
        self,
        providers: list[ProviderTier],
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        timeout_ms: int,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """成本优先流式：先尝试最便宜 provider，低质量/超时则升舱竞速。"""
        if not providers:
            yield {"type": "chunk", "data": ""}
            yield {
                "type": "meta",
                "strategy": AggregationStrategy.COST_FIRST.value,
                "winner": "aggregate",
                "latency_ms": 0,
                "cost_estimate": 0.0,
                "attempted": [],
                "failed": [],
            }
            return
        # 便宜/本地优先
        cheap_timeout_ms = min(2000, timeout_ms // 3)
        sorted_providers = sorted(providers, key=lambda p: (p.cost_per_1k_tokens, p.priority))
        first = sorted_providers[0]
        queue: asyncio.Queue[Any] = asyncio.Queue()

        task = asyncio.create_task(
            self._stream_one(0, first, messages, temperature, max_tokens, queue, **kwargs)
        )
        started = time.monotonic()
        first_chunk = ""
        first_ok = False
        deadline = started + timeout_ms / 1000.0
        try:
            # Phase 1: 等待便宜 provider 首个 chunk，判断质量
            while True:
                remaining = started + cheap_timeout_ms / 1000.0 - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                kind = item[1]
                if kind == "chunk":
                    chunk = item[2]
                    first_chunk += chunk
                    if self._is_quality_ok(first_chunk):
                        first_ok = True
                        yield {"type": "chunk", "data": first_chunk}
                        break
                elif kind == "done":
                    if self._is_quality_ok(first_chunk):
                        first_ok = True
                        if first_chunk:
                            yield {"type": "chunk", "data": first_chunk}
                    break
                elif kind == "error":
                    break
            if first_ok:
                # 便宜 provider 质量达标：继续输出剩余 chunk
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    kind = item[1]
                    if kind == "chunk":
                        yield {"type": "chunk", "data": item[2]}
                    elif kind == "done":
                        break
                    elif kind == "error":
                        break
                latency_ms = int((time.monotonic() - started) * 1000)
                yield {
                    "type": "meta",
                    "strategy": AggregationStrategy.COST_FIRST.value,
                    "winner": first.provider_name,
                    "latency_ms": latency_ms,
                    "cost_estimate": self._estimate_stream_cost(first, first_chunk),
                    "attempted": [first.provider_name],
                    "failed": [],
                }
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (Exception, asyncio.CancelledError):
                        return
                return
            # 升舱：取消便宜任务，剩余 provider 竞速
            if not task.done():
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
            remaining_providers = sorted_providers[1:]
            if not remaining_providers:
                yield {"type": "chunk", "data": first_chunk}
                yield {
                    "type": "meta",
                    "strategy": AggregationStrategy.COST_FIRST.value,
                    "winner": "aggregate",
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "cost_estimate": 0.0,
                    "attempted": [first.provider_name],
                    "failed": [first.provider_name],
                }
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (Exception, asyncio.CancelledError):
                        return
                return
            async for item in self._race_stream(
                remaining_providers,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_ms=int((deadline - time.monotonic()) * 1000),
                **kwargs,
            ):
                yield item
            if not task.done():
                task.cancel()
                try:
                    await task
                except (Exception, asyncio.CancelledError):
                    return
            return
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (Exception, asyncio.CancelledError):
                    pass

    def _estimate_stream_cost(self, provider: ProviderTier, content: str) -> float:
        """流式场景下按输出内容长度估算成本（无 token 用量时）。"""
        estimated_tokens = max(len(content) // 2, 1) if content else 0
        return estimated_tokens * provider.cost_per_1k_tokens / 1000


__all__ = [
    "AggregationStrategy",
    "ProviderTier",
    "AggregationResult",
    "ApiAggregator",
]
