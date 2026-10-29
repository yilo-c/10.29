"""敏感度分类器 — 个人知识体系隐私核心（保守默认）。

为原始信息条目（raw item）判定敏感度：
- sensitive : 仅本地 AI 处理，禁止出本机（云同步/云端 AI）
- personal  : 本地优先处理，可同步

策略（保守默认）：
1. 来源默认映射：微信/私聊/日记/剪贴板 → sensitive；阅读/资讯/网页 → personal
2. 关键词升级：内容命中敏感关键词（证件/财务/医疗/债务…）→ sensitive
3. 显式 override 优先
"""
from __future__ import annotations

import re
from typing import Any

# ── 来源默认敏感度（保守默认） ─────────────────────────────────
# 隐私直觉：私聊/日记/剪贴板默认涉密；公开信息源默认 personal
SOURCE_DEFAULT_SENSITIVITY: dict[str, str] = {
    # 私密来源 → sensitive
    "wechat": "sensitive",
    "clipboard": "sensitive",
    "daily": "sensitive",
    "voice": "sensitive",     # 语音转写常为私密对话
    # 公开来源 → personal
    "reading": "personal",
    "info": "personal",
    "url": "personal",
    "kindle": "personal",
    "bookmarks": "personal",
    "readwise": "personal",
    "browser": "personal",
    # AI 对话默认 personal（可含隐私，但默认不过度标记）
    "chatgpt": "personal",
    "claude": "personal",
    "kimi": "personal",
    "markdown": "personal",
    "inbox": "personal",
    "behavior": "personal",
    "note": "personal",
    "ocr": "personal",
}

# ── 敏感关键词 ───────────────────────────────────────────────
# 强信号词（≥3 字，命中即升级 sensitive）：
#   证件/财务/医疗/法律等，3 字即强信号，直接命中
_SENSITIVE_KEYWORDS_STRONG: tuple[str, ...] = (
    "身份证", "身份证号", "护照", "户口本", "驾照",
    "银行卡", "卡号", "信用卡", "支付密码", "转账密码", "支付宝密码",
    "微信支付密码", "银行账号", "余额", "工资", "薪水", "年终奖",
    "股票", "基金", "投资金额", "理财产品", "房贷", "车贷", "贷款",
    "借款", "欠款", "债务", "欠钱", "还钱",
    "体检报告", "病历", "诊断", "处方", "化验单", "用药", "过敏史",
    "抑郁症", "焦虑症", "慢性病", "住院",
    "房产证", "离婚", "诉讼", "律师费", "仲裁",
    "验证码",
)

# 弱信号词（易误伤：技术对话常见），需与隐私语境共现才命中
_SENSITIVE_KEYWORDS_WEAK: tuple[str, ...] = (
    "密码", "token", "api key", "apikey", "密钥",
)

# 弱信号词生效所需的语境词（账号/登录/支付/绑定等）
_PASSWORD_CONTEXT = ("账号", "账户", "登录", "支付", "银行卡", "验证码", "重置", "绑定", "登录名")


class SensitivityClassifier:
    """敏感度分类器（纯规则，无 AI 依赖，可离线）。"""

    def classify(
        self,
        content: str,
        source: str = "",
        *,
        kind: str = "text",
        override: str | None = None,
    ) -> str:
        """判定敏感度：personal | sensitive。override 显式指定时优先。"""
        if override in ("personal", "sensitive"):
            return override

        # 1. 来源默认
        default = SOURCE_DEFAULT_SENSITIVITY.get(source, "personal")
        if default == "sensitive":
            return "sensitive"

        # 2. 关键词升级（强信号词直接命中；弱信号词需语境）
        text = (content or "").lower()
        for kw in _SENSITIVE_KEYWORDS_STRONG:
            if kw.lower() in text:
                return "sensitive"
        for kw in _SENSITIVE_KEYWORDS_WEAK:
            if kw.lower() in text:
                for ctx in _PASSWORD_CONTEXT:
                    if ctx in text:
                        return "sensitive"
        return "personal"

    # ── 便捷 API ─────────────────────────────────────────────

    def for_item(self, item: dict[str, Any]) -> str:
        """对 raw item dict 判定（读 content/source/kind/meta.override）。"""
        meta = item.get("meta") or {}
        return self.classify(
            str(item.get("content") or ""),
            str(item.get("source") or ""),
            kind=str(item.get("kind") or "text"),
            override=meta.get("sensitivity_override"),
        )


# 便捷单例
_default_classifier: SensitivityClassifier | None = None


def get_classifier() -> SensitivityClassifier:
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = SensitivityClassifier()
    return _default_classifier
