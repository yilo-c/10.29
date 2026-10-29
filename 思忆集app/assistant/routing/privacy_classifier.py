"""隐私分类器：输入文本隐私级别分类。

根据 Android PrivacyClassifier.kt 移植到桌面端：
- classify(text, default_level) → PrivacyLevel
- is_personal_content(text) → bool
"""

from __future__ import annotations


class PrivacyLevel:
    """隐私级别常量（与 Android PrivacyLevel.kt 一致）。"""
    LOCAL_ONLY = "local_only"
    SYNCABLE = "syncable"
    SHAREABLE = "shareable"
    MODEL_EGRESS_ALLOWED = "model_egress_allowed"


class PrivacyClassifier:
    """基于关键词的输入文本隐私分类。

    作为 Judge LLM 隐私判断的补充安全网。
    """

    _SENSITIVE_PATTERNS = (
        "日记", "私人", "秘密", "隐私",
        "我的笔记", "我的任务", "我的日程",
        "密码", "账号", "私钥", "密钥",
        "银行卡", "身份证", "手机号", "电话号码",
        "家庭住址", "通讯录", "联系人",
        "健康", "病历", "体检", "医疗",
        "工资", "收入", "财务", "银行",
        "password", "secret", "private", "confidential",
        "my note", "my task", "my diary",
        "bank card", "credit card", "id card",
        "phone number", "address", "health record",
        "api key", "token", "credential",
    )

    _PERSONAL_PATTERNS = (
        "我的", "我的笔记", "我的任务",
        "my note", "my task", "my diary",
    )

    def classify(
        self,
        text: str,
        default_level: str = PrivacyLevel.SYNCABLE,
    ) -> str:
        """分类输入文本的隐私级别。

        Args:
            text: 用户输入文本。
            default_level: 无模式匹配时的默认级别。

        Returns:
            PrivacyLevel 常量。
        """
        if not text:
            return default_level
        lower = text.lower()
        for pattern in self._SENSITIVE_PATTERNS:
            if pattern in lower:
                return PrivacyLevel.LOCAL_ONLY
        return default_level

    def is_personal_content(self, text: str) -> bool:
        """判断文本是否包含个人信息/内容。"""
        if not text:
            return False
        lower = text.lower()
        return any(p in lower for p in self._PERSONAL_PATTERNS)
