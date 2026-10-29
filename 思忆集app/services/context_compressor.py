"""对话上下文压缩器 — 减少长对话的 token 消耗。

当对话消息超过 token 阈值时，将早期消息压缩为摘要，
保留最近的消息以保持对话连贯性。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("siyi.services.context_compressor")


def compress_text_head_tail(
    text: str,
    max_chars: int = 2000,
    *,
    head_ratio: float = 0.3,
    tail_ratio: float = 0.3,
) -> str:
    """P1-5: 压缩长文本输出，保留头尾关键信息。

    替代 ``text[:max_chars]`` 硬截断。策略：
    - 短文本（≤ max_chars）：原样返回
    - 长文本：保留前 ``head_ratio`` + 后 ``tail_ratio``，中间用
      ``[...已压缩 N 字...]`` 标记

    相比硬截断的优势：
    - 硬截断丢失结尾的结论/总结
    - 头尾保留能同时保留开头的问题分析和结尾的结论

    Args:
        text: 原始文本
        max_chars: 最大字符预算
        head_ratio: 头部保留比例（0-1）
        tail_ratio: 尾部保留比例（0-1）

    Returns:
        压缩后的文本（可能等于原文）
    """
    if not text or len(text) <= max_chars:
        return text

    head_budget = int(max_chars * head_ratio)
    tail_budget = int(max_chars * tail_ratio)
    compressed_count = len(text) - head_budget - tail_budget

    head = text[:head_budget]
    tail = text[-tail_budget:]
    result = f"{head}\n[...已压缩 {compressed_count} 字...]\n{tail}"

    logger.debug(
        "P1-5 输出压缩: %d → %d 字符 (节省 %d)",
        len(text),
        len(result),
        len(text) - len(result),
    )
    return result


@dataclass
class CompressionResult:
    """压缩结果"""

    messages: list[dict[str, str]]
    original_count: int
    compressed_count: int
    token_estimate: int
    was_compressed: bool
    summary: str | None = None


class TokenEstimator:
    """Token 估算器 — 基于字符数的近似估算。

    中文约 1.5 字符/token，英文约 4 字符/token。
    为保守估计，使用 2 字符/token 的混合比例。
    """

    # 不同语言的字符/token 比例
    CJK_RATIO = 1.5  # 中日韩文字
    LATIN_RATIO = 4.0  # 拉丁字母

    # CJK Unicode 范围
    CJK_RANGES = [
        (0x4E00, 0x9FFF),  # CJK 统一汉字
        (0x3400, 0x4DBF),  # CJK 扩展 A
        (0x3000, 0x303F),  # CJK 符号和标点
        (0x3040, 0x309F),  # 平假名
        (0x30A0, 0x30FF),  # 片假名
        (0xAC00, 0xD7AF),  # 韩文音节
    ]

    def __init__(self, avg_ratio: float = 2.5):
        """初始化估算器。

        Args:
            avg_ratio: 平均字符/token 比例，默认 2.5
        """
        self.avg_ratio = avg_ratio

    def estimate(self, text: str) -> int:
        """估算文本的 token 数。"""
        if not text:
            return 0

        cjk_count = 0
        latin_count = 0
        other_count = 0

        for char in text:
            code = ord(char)
            if any(start <= code <= end for start, end in self.CJK_RANGES):
                cjk_count += 1
            elif char.isascii():
                latin_count += 1
            else:
                other_count += 1

        # 分别计算各部分的 token 数
        cjk_tokens = cjk_count / self.CJK_RATIO
        latin_tokens = latin_count / self.LATIN_RATIO
        other_tokens = other_count / self.avg_ratio

        return int(cjk_tokens + latin_tokens + other_tokens) + 1

    def estimate_messages(self, messages: list[dict[str, str]]) -> int:
        """估算消息列表的总 token 数。"""
        total = 0
        for msg in messages:
            # 角色标记约 4 tokens
            total += 4
            # 内容
            content = msg.get("content", "")
            total += self.estimate(content)
            # 工具调用等额外字段
            if "tool_calls" in msg:
                total += self.estimate(json.dumps(msg["tool_calls"], ensure_ascii=False))
            if "name" in msg:
                total += self.estimate(msg["name"])
        return total


class ContextCompressor:
    """对话上下文压缩器。

    当消息超过 token 阈值时，压缩早期对话为摘要。

    使用示例：
        compressor = ContextCompressor(max_tokens=4000)
        result = compressor.compress(messages)
        if result.was_compressed:
            logger.info(f"压缩了 {result.original_count - result.compressed_count} 条消息")
        compressed_messages = result.messages
    """

    def __init__(
        self,
        max_tokens: int = 4000,
        keep_recent: int = 10,
        min_compress_count: int = 5,
        summarizer: Callable[[list[dict[str, str]]], str] | None = None,
    ):
        """初始化压缩器。

        Args:
            max_tokens: 最大 token 阈值，超过此值触发压缩
            keep_recent: 保留最近的消息数
            min_compress_count: 最少需要压缩的消息数（少于此数不压缩）
            summarizer: 自定义摘要函数，接收消息列表返回摘要文本
        """
        self.max_tokens = max_tokens
        self.keep_recent = keep_recent
        self.min_compress_count = min_compress_count
        self._token_estimator = TokenEstimator()
        self._summarizer = summarizer or self._default_summarize
        # 缓存摘要结果
        self._summary_cache: dict[str, str] = {}

    def _default_summarize(self, messages: list[dict[str, str]]) -> str:
        """默认摘要生成 — 提取关键信息。

        这是一个简单的提取式摘要，实际使用时应替换为
        调用本地 LLM 生成更好的摘要。
        """
        if not messages:
            return ""

        parts = []

        # 提取用户消息的关键内容
        user_messages = [m for m in messages if m.get("role") == "user"]
        assistant_messages = [m for m in messages if m.get("role") == "assistant"]

        # 统计对话主题
        topics = set()
        for msg in user_messages[:5]:  # 只看前5条用户消息
            content = msg.get("content", "")
            # 提取问句主题（简单规则）
            if "?" in content or "？" in content:
                # 提取问句的前30个字符作为主题
                match = re.search(r"([^?？]{5,30})[?？]", content)
                if match:
                    topics.add(match.group(1).strip())

        if topics:
            parts.append(f"讨论主题: {', '.join(list(topics)[:3])}")

        # 统计消息数
        parts.append(f"共 {len(user_messages)} 条用户消息和 {len(assistant_messages)} 条助手回复")

        # 提取最后几条消息的关键信息
        recent = messages[-3:] if len(messages) >= 3 else messages
        for msg in recent:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:100]
            if content:
                parts.append(f"[{role}] {content}...")

        return " | ".join(parts)

    def _get_messages_hash(self, messages: list[dict[str, str]]) -> str:
        """计算消息列表的哈希值（用于缓存）。"""
        content = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()[:12]

    def compress(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
    ) -> CompressionResult:
        """压缩消息列表。

        Args:
            messages: 原始消息列表
            system_prompt: 系统提示（不参与压缩）

        Returns:
            CompressionResult: 压缩结果
        """
        original_count = len(messages)
        token_estimate = self._token_estimator.estimate_messages(messages)

        # 未超过阈值，不需要压缩
        if token_estimate <= self.max_tokens:
            return CompressionResult(
                messages=messages,
                original_count=original_count,
                compressed_count=original_count,
                token_estimate=token_estimate,
                was_compressed=False,
            )

        # 分离系统消息
        system_messages = [m for m in messages if m.get("role") == "system"]
        non_system_messages = [m for m in messages if m.get("role") != "system"]

        # 消息太少，不压缩
        if len(non_system_messages) < self.min_compress_count + self.keep_recent:
            return CompressionResult(
                messages=messages,
                original_count=original_count,
                compressed_count=original_count,
                token_estimate=token_estimate,
                was_compressed=False,
            )

        # 分割要压缩和要保留的消息
        to_compress = non_system_messages[: -self.keep_recent]
        to_keep = non_system_messages[-self.keep_recent :]

        # 检查缓存
        cache_key = self._get_messages_hash(to_compress)
        if cache_key in self._summary_cache:
            summary = self._summary_cache[cache_key]
        else:
            # 生成摘要
            summary = self._summarizer(to_compress)
            # 缓存摘要
            self._summary_cache[cache_key] = summary
            # 限制缓存大小
            if len(self._summary_cache) > 100:
                # 删除最早的缓存
                oldest_key = next(iter(self._summary_cache))
                del self._summary_cache[oldest_key]

        # 构建压缩后的消息列表
        compressed_messages = []

        # 保留系统消息
        compressed_messages.extend(system_messages)

        # 添加摘要作为系统消息
        if summary:
            compressed_messages.append(
                {
                    "role": "system",
                    "content": f"[历史对话摘要] {summary}",
                }
            )

        # 添加保留的最近消息
        compressed_messages.extend(to_keep)

        compressed_count = len(compressed_messages)
        new_token_estimate = self._token_estimator.estimate_messages(compressed_messages)

        logger.info("上下文压缩: %d 条消息 → %d 条消息, token: %d → %d", original_count, compressed_count, token_estimate, new_token_estimate)

        return CompressionResult(
            messages=compressed_messages,
            original_count=original_count,
            compressed_count=compressed_count,
            token_estimate=new_token_estimate,
            was_compressed=True,
            summary=summary,
        )

    def clear_cache(self) -> None:
        """清空摘要缓存。"""
        self._summary_cache.clear()

    def get_cache_stats(self) -> dict[str, int]:
        """获取缓存统计。"""
        return {
            "cache_size": len(self._summary_cache),
        }


class LLMSummarizer:
    """使用 LLM 生成对话摘要。

    需要注入一个 AI 客户端来生成摘要。
    """

    def __init__(self, ai_client: Any, max_summary_tokens: int = 200):
        """初始化 LLM 摘要器。

        Args:
            ai_client: AI 客户端（需要有 chat_completion 方法）
            max_summary_tokens: 摘要最大 token 数
        """
        self.ai_client = ai_client
        self.max_summary_tokens = max_summary_tokens

    def summarize(self, messages: list[dict[str, str]]) -> str:
        """使用 LLM 生成摘要。"""
        if not messages:
            return ""

        # 构建摘要请求
        conversation_text = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if content:
                conversation_text.append(f"{role}: {content}")

        full_text = "\n".join(conversation_text)

        # 截断过长的文本
        if len(full_text) > 8000:
            full_text = full_text[:4000] + "\n...\n" + full_text[-4000:]

        prompt = f"""请为以下对话生成一个简洁的摘要，包含：
1. 主要讨论主题
2. 关键信息点
3. 用户的主要需求或问题

对话内容：
{full_text}

请用中文输出摘要，控制在 200 字以内。"""

        try:
            response = self.ai_client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=self.max_summary_tokens,
            )
            return response["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("LLM 摘要生成失败，回退到简单摘要: %s", e)
            # 回退到简单摘要
            return ContextCompressor()._default_summarize(messages)


class SmartCompressor(ContextCompressor):
    """智能上下文压缩器 — 基于重要性的压缩。

    相比基础压缩器，增加以下能力：
    1. 基于重要性的消息保留（工具调用结果、关键决策等）
    2. 工具调用结果的压缩（只保留摘要）
    3. 渐进式压缩策略（先压缩不重要的，再压缩重要的）
    """

    # 消息重要性评分关键词
    HIGH_IMPORTANCE_KEYWORDS = [
        "决定",
        "结论",
        "总结",
        "重要",
        "关键",
        "注意",
        "记住",
        "保存",
        "创建",
        "删除",
        "修改",
        "更新",
        "任务",
        "待办",
        "提醒",
        "计划",
        "目标",
    ]

    def __init__(
        self,
        max_tokens: int = 4000,
        keep_recent: int = 10,
        min_compress_count: int = 5,
        summarizer: Callable[[list[dict[str, str]]], str] | None = None,
        importance_threshold: float = 0.7,
    ):
        """初始化智能压缩器。

        Args:
            max_tokens: 最大 token 阈值
            keep_recent: 保留最近的消息数
            min_compress_count: 最少需要压缩的消息数
            summarizer: 自定义摘要函数
            importance_threshold: 重要性阈值（0-1），高于此值的消息优先保留
        """
        super().__init__(max_tokens, keep_recent, min_compress_count, summarizer)
        self.importance_threshold = importance_threshold
        self._token_estimator = TokenEstimator()

    def _calculate_importance(self, message: dict[str, str]) -> float:
        """计算消息的重要性分数（0-1）。"""
        content = message.get("content", "")
        role = message.get("role", "")

        if not content:
            return 0.0

        score = 0.0

        # 1. 角色权重
        if role == "system":
            return 1.0  # 系统消息最重要
        elif role == "user":
            score += 0.3
        elif role == "assistant":
            score += 0.2

        # 2. 关键词匹配
        keyword_matches = sum(1 for kw in self.HIGH_IMPORTANCE_KEYWORDS if kw in content)
        score += min(0.4, keyword_matches * 0.1)

        # 3. 长度权重（过短或过长都降低分数）
        content_len = len(content)
        if 50 <= content_len <= 500:
            score += 0.2
        elif content_len > 500:
            score += 0.1

        # 4. 工具调用结果
        if "tool_calls" in message or "tool_call_id" in message:
            score += 0.3

        # 5. 包含数字/列表（通常是关键信息）
        if any(c.isdigit() for c in content):
            score += 0.1
        if "-" in content or "•" in content or "1." in content:
            score += 0.1

        return min(1.0, score)

    def _compress_tool_results(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """压缩工具调用结果 — 只保留摘要。"""
        compressed = []
        for msg in messages:
            if msg.get("role") == "tool" and "content" in msg:
                content = msg.get("content", "")
                # 如果工具结果过长，截取关键部分
                if len(content) > 500:
                    # 保留开头和结尾
                    summary = content[:200] + "\n...[已压缩]...\n" + content[-200:]
                    compressed.append(
                        {
                            **msg,
                            "content": f"[工具结果摘要] {summary}",
                            "_original_length": len(content),
                        }
                    )
                else:
                    compressed.append(msg)
            else:
                compressed.append(msg)
        return compressed

    def compress(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
    ) -> CompressionResult:
        """智能压缩消息列表。"""
        original_count = len(messages)

        # 先压缩工具调用结果
        messages = self._compress_tool_results(messages)

        # 计算每条消息的重要性
        scored_messages = [(msg, self._calculate_importance(msg)) for msg in messages]

        # 按重要性排序，但保留原始顺序用于压缩
        token_estimate = self._token_estimator.estimate_messages(messages)

        if token_estimate <= self.max_tokens:
            return CompressionResult(
                messages=messages,
                original_count=original_count,
                compressed_count=len(messages),
                token_estimate=token_estimate,
                was_compressed=False,
            )

        # 分离系统消息
        system_messages = [m for m in messages if m.get("role") == "system"]
        non_system_messages = [m for m in messages if m.get("role") != "system"]

        if len(non_system_messages) < self.min_compress_count + self.keep_recent:
            return CompressionResult(
                messages=messages,
                original_count=original_count,
                compressed_count=len(messages),
                token_estimate=token_estimate,
                was_compressed=False,
            )

        # 分割要压缩和要保留的消息
        to_compress = non_system_messages[: -self.keep_recent]
        to_keep = non_system_messages[-self.keep_recent :]

        # 在要压缩的消息中，优先保留高重要性消息的摘要
        high_importance = [msg for msg, score in scored_messages[: -self.keep_recent] if score >= self.importance_threshold]

        # 生成摘要
        cache_key = self._get_messages_hash(to_compress)
        if cache_key in self._summary_cache:
            summary = self._summary_cache[cache_key]
        else:
            summary = self._summarizer(to_compress)
            # 如果有高重要性消息，追加到摘要
            if high_importance:
                important_points = []
                for msg in high_importance[:3]:
                    content = msg.get("content", "")[:100]
                    if content:
                        important_points.append(f"• {content}")
                if important_points:
                    summary += "\n\n关键信息:\n" + "\n".join(important_points)

            self._summary_cache[cache_key] = summary
            if len(self._summary_cache) > 100:
                oldest_key = next(iter(self._summary_cache))
                del self._summary_cache[oldest_key]

        # 构建压缩后的消息列表
        compressed_messages = list(system_messages)

        if summary:
            compressed_messages.append(
                {
                    "role": "system",
                    "content": f"[历史对话摘要] {summary}",
                }
            )

        compressed_messages.extend(to_keep)

        compressed_count = len(compressed_messages)
        new_token_estimate = self._token_estimator.estimate_messages(compressed_messages)

        logger.info(
            "智能上下文压缩: %d 条消息 → %d 条消息, token: %d → %d (高重要性消息: %d)",
            original_count,
            compressed_count,
            token_estimate,
            new_token_estimate,
            len(high_importance),
        )

        return CompressionResult(
            messages=compressed_messages,
            original_count=original_count,
            compressed_count=compressed_count,
            token_estimate=new_token_estimate,
            was_compressed=True,
            summary=summary,
        )


# 便捷函数
def compress_context(
    messages: list[dict[str, str]],
    max_tokens: int = 4000,
    ai_client: Any | None = None,
) -> CompressionResult:
    """压缩对话上下文的便捷函数。

    Args:
        messages: 消息列表
        max_tokens: 最大 token 阈值
        ai_client: 可选的 AI 客户端（用于生成更好的摘要）

    Returns:
        CompressionResult: 压缩结果
    """
    summarizer = None
    if ai_client is not None:
        llm_summarizer = LLMSummarizer(ai_client)
        summarizer = llm_summarizer.summarize

    compressor = ContextCompressor(
        max_tokens=max_tokens,
        summarizer=summarizer,
    )
    return compressor.compress(messages)


def compress_context_smart(
    messages: list[dict[str, str]],
    max_tokens: int = 4000,
    ai_client: Any | None = None,
    keep_recent: int = 10,
    importance_threshold: float = 0.7,
) -> CompressionResult:
    """P1.7: 智能压缩对话上下文。

    与 ``compress_context`` 相比：
    - 使用 :class:`SmartCompressor`（基于重要性评分保留关键消息）
    - 启用 LLM 摘要（若提供 ``ai_client``），失败自动回退到提取式摘要
    - 压缩工具调用结果（>500 字截取关键部分）

    Args:
        messages: 消息列表
        max_tokens: 最大 token 阈值
        ai_client: 可选的 AI 客户端（启用 LLM 摘要）
        keep_recent: 保留最近的消息数
        importance_threshold: 重要性阈值（0-1）

    Returns:
        CompressionResult: 压缩结果
    """
    summarizer = None
    if ai_client is not None:
        llm_summarizer = LLMSummarizer(ai_client)
        summarizer = llm_summarizer.summarize

    compressor = SmartCompressor(
        max_tokens=max_tokens,
        keep_recent=keep_recent,
        summarizer=summarizer,
        importance_threshold=importance_threshold,
    )
    return compressor.compress(messages)
