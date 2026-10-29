from __future__ import annotations

from typing import ClassVar


class DispatchResult:
    """统一路由返回结果。

    is_handled: 是否已被路由处理（非 CHAT 意图）
    intent:     INTENT_CHAT / INTENT_EXECUTE / INTENT_AUTOMATE
    data:       路由数据负载（dict）
    """

    def __init__(
        self,
        is_handled: bool = False,
        intent: str = "",
        data: dict[str, object] | None = None,
    ):
        self.is_handled = is_handled
        self.intent = intent
        self.data = data or {}

    def __getitem__(self, key: str) -> object:
        """兼容 dict 风格访问（遗留代码需要）。"""
        if key == "is_handled":
            return self.is_handled
        if key == "intent":
            return self.intent
        if key == "data":
            return self.data
        return self.data.get(key) if isinstance(self.data, dict) else None

    @classmethod
    def handled(
        cls,
        intent: str,
        data: dict[str, object] | None = None,
    ) -> "DispatchResult":
        return cls(is_handled=True, intent=intent, data=data)

    @classmethod
    def not_handled(
        cls,
        intent: str = "",
        data: dict[str, object] | None = None,
    ) -> "DispatchResult":
        return cls(is_handled=False, intent=intent, data=data)
