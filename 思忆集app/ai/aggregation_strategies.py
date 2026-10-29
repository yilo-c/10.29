# -*- coding: utf-8 -*-
"""API 聚合策略实现。

提供多种多 provider 结果聚合/选择策略：
- RACE: 竞速，取第一个成功结果
- VOTE: 投票，多数一致胜出
- BEST_OF_N: 择优，用 AI 评判选最佳
- COST_FIRST: 成本优先，先本地/便宜 provider，超时再升舱
- MERGE: 合并，多视角融合为一份综合报告
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger("siyi.ai.aggregation")


@dataclass
class CandidateResult:
    """单个 provider 的候选结果。"""

    provider_name: str
    model: str
    content: str
    latency_ms: int
    success: bool
    error: str = ""
    tokens_input: int = 0
    tokens_output: int = 0


#: 聚合函数签名：候选结果列表 -> 胜出结果
AggregationFn = Callable[[list[CandidateResult]], Awaitable[CandidateResult]]


def _sanitize_content(text: Any) -> str:
    """将任意输出统一为字符串。"""
    if text is None:
        return ""
    if isinstance(text, str):
        return text.strip()
    if isinstance(text, dict):
        return str(text.get("content") or "").strip()
    return str(text).strip()


def _judge_best_prompt(results: list[CandidateResult], original_question: str) -> str:
    """BEST_OF_N 策略使用的评判 prompt。"""
    prompt = (
        f"你是一位严格的评判员。以下是多个 AI 对同一问题的回答，请选出最佳答案并直接输出该答案的原文，不要添加额外解释。\n\n问题：{original_question}\n\n"
    )
    for idx, r in enumerate(results, 1):
        # 转义回答内的 ``` 防止评判模型把它当成 prompt 结构
        content = r.content.replace("```", "`\u200b`\u200b`")
        prompt += f"--- 回答 {idx}（{r.provider_name}/{r.model}）---\n{content}\n\n"
    prompt += "最佳答案："
    return prompt


def _merge_prompt(results: list[CandidateResult], original_question: str) -> str:
    """MERGE 策略使用的融合 prompt。"""
    prompt = (
        f"请将以下多个 AI 的回答融合为一份完整、连贯、无重复的综合回答。保留各自有价值的观点，消除矛盾，按逻辑组织。\n\n问题：{original_question}\n\n"
    )
    for idx, r in enumerate(results, 1):
        content = r.content.replace("```", "`\u200b`\u200b`")
        prompt += f"--- 角度 {idx}（{r.provider_name}/{r.model}）---\n{content}\n\n"
    prompt += "综合回答："
    return prompt


async def race_strategy(results: list[CandidateResult]) -> CandidateResult:
    """竞速：结果列表中只应包含第一个完成的（调用层已处理）。"""
    success_results = [r for r in results if r.success and r.content]
    if success_results:
        return success_results[0]
    return CandidateResult(
        provider_name="aggregate",
        model="race",
        content="",
        latency_ms=0,
        success=False,
        error="所有 provider 均失败或返回空",
    )


async def vote_strategy(results: list[CandidateResult]) -> CandidateResult:
    """投票：提取核心结论，多数一致胜出；不一致时返回第一个并标注。"""
    success_results = [r for r in results if r.success and r.content]
    if not success_results:
        return CandidateResult(
            provider_name="aggregate",
            model="vote",
            content="",
            latency_ms=0,
            success=False,
            error="所有 provider 均失败",
        )
    if len(success_results) == 1:
        return success_results[0]
    # 按前 200 字归组投票
    groups: dict[str, list[CandidateResult]] = {}
    for r in success_results:
        key = r.content[:200].strip()
        groups.setdefault(key, []).append(r)
    best_group = max(groups.values(), key=lambda g: len(g))
    winner = best_group[0]
    if len(best_group) >= len(success_results) / 2:
        return winner
    # 无多数一致：返回第一个回答并附加提示
    first = success_results[0]
    combined = (
        f"{first.content}\n\n[聚合提示："
        f"{len(success_results)} 个 provider 回答不一致，以上为 "
        f"{first.provider_name} 的回答]"
    )
    return CandidateResult(
        provider_name="aggregate/vote",
        model="vote",
        content=combined,
        latency_ms=first.latency_ms,
        success=True,
    )


async def best_of_n_strategy(
    results: list[CandidateResult],
    original_question: str,
    judge_call: Callable[[list[dict[str, str]]], Awaitable[str]],
) -> CandidateResult:
    """择优：用 AI 评判选最佳答案。"""
    success_results = [r for r in results if r.success and r.content]
    if not success_results:
        return CandidateResult(
            provider_name="aggregate",
            model="best_of_n",
            content="",
            latency_ms=0,
            success=False,
            error="所有 provider 均失败",
        )
    if len(success_results) == 1:
        return success_results[0]
    prompt = _judge_best_prompt(success_results, original_question)
    try:
        best_content = await judge_call([{"role": "user", "content": prompt}])
        best_content = _sanitize_content(best_content)
        # 评判输出若与某候选完全一致，则归属该 provider
        winner = next((r for r in success_results if r.content == best_content), None)
        return CandidateResult(
            provider_name=winner.provider_name if winner else "aggregate/best_of_n",
            model=winner.model if winner else "best_of_n",
            content=best_content or success_results[0].content,
            latency_ms=max(r.latency_ms for r in success_results),
            success=True,
        )
    except Exception as exc:
        logger.warning("best_of_n judge failed: %s", exc)
        return success_results[0]


async def cost_first_strategy(
    providers: list[dict[str, Any]],
    messages: list[dict[str, str]],
    call_fn: Callable[[dict[str, Any], list[dict[str, str]]], Awaitable[CandidateResult]],
    cheap_timeout_ms: int = 2000,
    quality_threshold: float = 0.8,
) -> CandidateResult:
    """成本优先：先本地/便宜 provider，超时或低质量再升舱。

    providers 必须按成本从低到高排序。
    """
    if not providers:
        return CandidateResult(
            provider_name="aggregate",
            model="cost_first",
            content="",
            latency_ms=0,
            success=False,
            error="无可用 provider",
        )
    # 第一层：最便宜的 provider，限时等待
    first = providers[0]
    try:
        result = await asyncio.wait_for(
            call_fn(first, messages),
            timeout=cheap_timeout_ms / 1000.0,
        )
        # 质量兜底：内容过短或明显拒答视为低质量，升舱
        if (
            result.success
            and result.content
            and len(result.content) >= 10
            and "无法" not in result.content[:50]
        ):
            return result
    except asyncio.TimeoutError:
        logger.debug("cost_first: %s timeout, upgrade to next tier", first.get("provider_name"))
    except Exception as exc:
        logger.debug("cost_first: %s failed: %s", first.get("provider_name"), exc)
    # 升舱：剩余 provider 竞速
    if len(providers) == 1:
        return CandidateResult(
            provider_name="aggregate",
            model="cost_first",
            content="",
            latency_ms=0,
            success=False,
            error="唯一 provider 失败或超时",
        )
    remaining = providers[1:]
    tasks: list[asyncio.Task[Any]] = [asyncio.create_task(call_fn(p, messages)) for p in remaining]

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        try:
            result = task.result()
            if result.success and result.content:
                return result
        except Exception as exc:
            logger.debug("cost_first: secondary provider failed: %s", exc)
    return CandidateResult(
        provider_name="aggregate",
        model="cost_first",
        content="",
        latency_ms=0,
        success=False,
        error="所有 tier 均失败或超时",
    )


async def merge_strategy(
    results: list[CandidateResult],
    original_question: str,
    merge_call: Callable[[list[dict[str, str]]], Awaitable[str]],
) -> CandidateResult:
    """合并：多视角融合为一份综合报告。"""
    success_results = [r for r in results if r.success and r.content]
    if not success_results:
        return CandidateResult(
            provider_name="aggregate",
            model="merge",
            content="",
            latency_ms=0,
            success=False,
            error="所有 provider 均失败",
        )
    if len(success_results) == 1:
        return success_results[0]
    prompt = _merge_prompt(success_results, original_question)
    try:
        merged = await merge_call([{"role": "user", "content": prompt}])
        merged = _sanitize_content(merged)
        return CandidateResult(
            provider_name="aggregate/merge",
            model="merge",
            content=merged or success_results[0].content,
            latency_ms=max(r.latency_ms for r in success_results),
            success=True,
        )
    except Exception as exc:
        logger.warning("merge failed: %s", exc)
        return success_results[0]


__all__ = [
    "CandidateResult",
    "AggregationFn",
    "race_strategy",
    "vote_strategy",
    "best_of_n_strategy",
    "cost_first_strategy",
    "merge_strategy",
]
