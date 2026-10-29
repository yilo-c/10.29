"""AI 客户端消费者声明 + 注入工具（P2-5 批次 B）。

把 ``AppContext.recreate_ai_client`` 中针对 6 个消费者（intent_router /
browser_assistant_bridge / autonomous_assistant_manager / copilot_engine /
suggestion_engine / inbox_capture_service）的 18 处 ``hasattr`` 层叠 if
分支收敛为统一的声明式列表：

    ┌─────────────────────────────────────────────────┐
    │ AppContext.recreate_ai_client(new_client)       │
    │   └─ apply_ai_client_bindings(self, new_client) │
    └────────────┬────────────────────────────────────┘
                 │ 遍历 DEFAULT_AI_CLIENT_BINDINGS
                 ▼
    ┌─────────────────────────────────────────────────┐
    │ AIClientBinding(attr, method?, fields, ...)     │
    │   apply(ctx, client) -> bool                    │
    └─────────────────────────────────────────────────┘

引入 Binding 的好处：
* 6 个消费者的属性差异（``set_ai_client`` 方法、``ai_client`` /
  ``_ai_client`` / ``ai_service`` 字段、``suggestion_engine`` 需非 None
  才更新）以 dataclass 字段统一表达；
* 新增消费者只需在 ``DEFAULT_AI_CLIENT_BINDINGS`` 末尾追加一行，无需
  再写 ``if hasattr(self, ...) and hasattr(..., ...)`` 层叠分支；
* 外部模块若实现 ``AIClientConsumer`` Protocol 可直接由 Binding 委托
  ``update_ai_client`` 方法，避免侵入式属性赋值。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("siyi.app_runtime.ai_client_consumers")


@runtime_checkable
class AIClientConsumer(Protocol):
    """显式实现的 AI 客户端消费者。

    实现此协议的对象会被 ``AIClientBinding`` 优先识别（``method``
    字段命中时直接调用方法），无需暴露内部字段。
    """

    def update_ai_client(self, new_client: Any) -> None: ...


@dataclass
class AIClientBinding:
    """声明式描述「如何把新的 ai_client 注入到 ``AppContext`` 某个子对象」。

    Parameters
    ----------
    attr:
        ``AppContext`` 上的属性名（例如 ``"intent_router"``）。
    method:
        优先尝试调用的 setter 方法名（例如 ``"set_ai_client"``）。
        命中时调用 ``target.<method>(new_client)`` 并返回。
    fields:
        method 不存在时回退尝试的属性名序列（按顺序，首个存在的字段被赋值）。
        默认为 ``("ai_client",)``，覆盖最常见用法。
    require_not_none:
        若为 True，仅当原字段值不为 None 时才更新（用于 ``suggestion_engine``
        ``_ai_client`` 这类"未初始化时跳过"的场景）。
    """

    attr: str
    method: str | None = None
    fields: tuple[str, ...] = ("ai_client",)
    require_not_none: bool = False

    def apply(self, ctx: Any, new_client: Any) -> bool:
        """尝试把 ``new_client`` 注入到 ``ctx`` 上 ``attr`` 所指对象。

        Returns
        -------
        bool
            True 表示成功注入；False 表示 attr 缺失 / target 为 None /
            没有匹配的 method 或 fields / require_not_none 校验未通过。
        """
        target = getattr(ctx, self.attr, None)
        if target is None:
            return False

        if self.method is not None and hasattr(target, self.method):
            getattr(target, self.method)(new_client)
            return True

        for f in self.fields:
            if not hasattr(target, f):
                continue
            if self.require_not_none and getattr(target, f) is None:
                return False
            setattr(target, f, new_client)
            return True

        return False


# ═══════════════════════════════════════════════════════════════════
# 默认消费者列表
# ═══════════════════════════════════════════════════════════════════
#
# 顺序与原 ``recreate_ai_client`` 一致；任何一项 apply 抛异常都会被
# ``apply_ai_client_bindings`` 捕获并 log，不影响其他 binding。


DEFAULT_AI_CLIENT_BINDINGS: tuple[AIClientBinding, ...] = (
    # IntentRouter：优先调 set_ai_client；回退 _ai_client / ai_client 字段
    AIClientBinding(
        attr="intent_router",
        method="set_ai_client",
        fields=("_ai_client", "ai_client"),
    ),
    # BrowserAssistantBridge：用 ai_service 字段名（与其他消费者不同）
    AIClientBinding(attr="browser_assistant_bridge", fields=("ai_service",)),
    # AutonomousAssistantManager：优先调 update_ai_client（同步 router/
    # computer_use/react_engine 子组件）；回退标准 ai_client 字段
    AIClientBinding(attr="autonomous_assistant_manager", method="update_ai_client"),
    # CopilotEngine：使用 _ai_client 私有属性优先；回退 ai_client
    AIClientBinding(attr="copilot_engine", fields=("_ai_client", "ai_client")),
    # SuggestionEngine：仅当 _ai_client 非 None 才覆盖（未启用时跳过）
    AIClientBinding(attr="suggestion_engine", fields=("_ai_client",), require_not_none=True),
    # InboxCaptureService：标准 ai_client 字段
    AIClientBinding(attr="inbox_capture_service"),
    # JobHunterService：使用 set_ai_client 方法
    AIClientBinding(attr="job_hunter_service", method="set_ai_client"),
)


def apply_ai_client_bindings(
    ctx: Any,
    new_client: Any,
    *,
    bindings: tuple[AIClientBinding, ...] | list[AIClientBinding] = DEFAULT_AI_CLIENT_BINDINGS,
) -> list[str]:
    """对 ``ctx`` 上每个 binding 尝试注入 ``new_client``。

    Returns
    -------
    list[str]
        成功注入的 ``attr`` 名列表（便于日志/测试断言）。
    """
    applied: list[str] = []
    for binding in bindings:
        try:
            if binding.apply(ctx, new_client):
                applied.append(binding.attr)
        except Exception:
            logger.warning(
                "AIClientBinding.apply failed: attr=%s", binding.attr, exc_info=True
            )
    return applied


__all__ = [
    "AIClientConsumer",
    "AIClientBinding",
    "DEFAULT_AI_CLIENT_BINDINGS",
    "apply_ai_client_bindings",
]
