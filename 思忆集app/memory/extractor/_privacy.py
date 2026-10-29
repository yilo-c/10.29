"""Privacy filter for memory extraction — sensitive data detection.

Extracted from memory/extractor.py — PrivacyFilter, _SENSITIVE_PATTERNS, _SENSITIVE_KEYWORDS.
"""
from __future__ import annotations

import logging
import re

from ..types import MemoryCandidate

logger = logging.getLogger("siyi.memory.extractor.privacy")

_SENSITIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"密码\s*[是=:：]\s*\S+", re.IGNORECASE),
    re.compile(r"(?:password|pwd|passwd)\s*[=:]\s*\S+", re.IGNORECASE),
    # P2-2: 银行卡号收紧到 13-19 位（VISA/MasterCard/银联实际位数），避免 \d{10,} 把超长金额数字也判为卡号。
    re.compile(r"(?:银行卡|信用卡|借记卡)\s*(?:号|号码|卡号)?\s*[是=:：]?\s*\d{13,19}", re.IGNORECASE),
    re.compile(r"(?:credit card|bank card|debit card)\s*(?:number|no|#)?\s*[=:]\s*\d{13,19}", re.IGNORECASE),
    re.compile(r"(?:api[_-]?key|api[_-]?secret|access[_-]?token)\s*[=:]\s*\S+", re.IGNORECASE),
    # P2-3: 显式 "token = xxx" / "token: xxx" / "token 是 xxx"（不再依赖宽泛关键词 "token"）。
    re.compile(r"\btoken\b\s*[=:是：]\s*\S+", re.IGNORECASE),
    re.compile(r"(?:私钥|密钥)\s*[是=:：]\s*\S+", re.IGNORECASE),
    # P2-2: 身份证号收紧为 17 位数字 + 1 位（数字或 X/x），避免 18 个连续 X 误命中。
    re.compile(r"(?:身份证|ID card|身份证号)\s*[是=:：]?\s*\d{17}[\dXx]", re.IGNORECASE),
]

# P2-3: 移除宽泛的独立 "token"（已由上方专用 pattern + access_token pattern 覆盖），
# 保留真正具有领域意义、单词出现即视为敏感的关键词。
_SENSITIVE_KEYWORDS: set[str] = {
    "密码", "口令", "密钥", "私钥", "验证码",
    "password", "secret", "private key", "api key",
}

# P2-3: 否定语境前缀 — 若关键词出现在以下短语中，不视为敏感（避免 "我没有密码" 被误判）。
_NEGATION_PREFIXES: tuple[str, ...] = (
    "没有", "不是", "不要", "不用", "无需", "不需要", "未设置", "尚未",
    "no ", "not ", "without ", "don't have ", "do not have ",
)


def _is_keyword_negated(text_lower: str, keyword: str) -> bool:
    """检测关键词命中处是否处于否定语境（用于 P2-3 误判规避）。

    仅检查关键词左侧紧邻的窗口（≤16 字符），允许出现 "don't have a password"
    这类否定中间夹冠词的情况。窗口太大会把句首否定扩散到后段，已通过单测覆盖
    `_multiple_occurrences_one_negated_one_not` 的边界。
    """
    idx = text_lower.find(keyword)
    while idx != -1:
        window_start = max(0, idx - 16)
        window = text_lower[window_start:idx]
        if any(prefix.strip() in window for prefix in _NEGATION_PREFIXES):
            next_idx = text_lower.find(keyword, idx + len(keyword))
            if next_idx == -1:
                return True
            idx = next_idx
            continue
        return False
    return False


class PrivacyFilter:
    """Filters sensitive information from extraction candidates.

    Uses keyword matching and pattern detection to prevent extraction of
    passwords, API keys, banking info, and other sensitive data.
    """

    @staticmethod
    def is_sensitive(text: str) -> bool:
        """Check if text contains sensitive information."""
        if not text:
            return False

        lower = text.lower()
        for kw in _SENSITIVE_KEYWORDS:
            if kw in lower and not _is_keyword_negated(lower, kw):
                return True

        for pattern in _SENSITIVE_PATTERNS:
            if pattern.search(text):
                return True

        return False

    @staticmethod
    def filter_candidates(
        candidates: list[MemoryCandidate],
    ) -> tuple[list[MemoryCandidate], list[MemoryCandidate]]:
        """Split candidates into safe and sensitive lists, tagging with privacy level."""
        safe: list[MemoryCandidate] = []
        sensitive: list[MemoryCandidate] = []
        for c in candidates:
            if PrivacyFilter.is_sensitive(c.content):
                c.privacy_level = "local_only"
                sensitive.append(c)
                logger.info("Filtered sensitive memory candidate: %s...", c.content[:40])
            else:
                c.privacy_level = "shareable"
                safe.append(c)
        return safe, sensitive


__all__ = [
    "_SENSITIVE_PATTERNS",
    "_SENSITIVE_KEYWORDS",
    "PrivacyFilter",
]
