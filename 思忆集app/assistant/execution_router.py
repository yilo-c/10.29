"""ExecutionEngine 接口 + ExecutionRouter（P0-11）.

把原 ``AutonomousAssistantManager`` 中混用的 5 个执行引擎收敛为统一接口：

    ┌──────────────────────────┐
    │     ExecutionRouter      │  ← AAM.dispatch_user_input 调用
    └──────────┬───────────────┘
               │ 按优先级遍历
               ▼
    ┌──────────────────────────────────────────┐
    │  ExecutionEngine (Protocol)              │
    │    can_handle(text, intent, ...) -> bool │
    │    execute(text, intent, ...) -> Result  │
    └──────────────────────────────────────────┘
               ▲
       ┌───────┴────────┬────────────────┐
       │                │                │
   ReActEngine      ComputerUse      GoalRunEngine
   Adapter            Adapter          Adapter
   (优先级 0)        (优先级 10)       (优先级 20)

接口约束：
* ``can_handle`` 廉价、无副作用，可以根据 text/intent/classification 决定是否承接。
* ``execute`` 返回 ``DispatchResult`` 表示已承接；返回 ``None`` 表示让下家继续尝试。
* 任意引擎抛异常都被 router 捕获并 log，不影响其他引擎。

引入 router 的好处：
* AAM.dispatch_user_input 不再硬编码 "ReAct 先试 / 关键字命中 / 否则 run_goal"；
* 新增引擎（例如 Codex 直跑、Agent Chain 直跑）只需注册到 router；
* 单元测试可以独立构造 router、注入 fake engine 验证调度。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from .dispatch_result import DispatchResult
from assistant.routing.intent_router import INTENT_CHAT, INTENT_EXECUTE

logger = logging.getLogger("siyi.assistant.execution_router")


@dataclass
class DispatchContext:
    """单次 dispatch 调用的可选参数；engine 按需取用。"""

    source: str = "assistant.goal"
    delivery_channel: Optional[str] = None
    chat_session_id: Optional[str] = None
    cancellation_event: Optional[threading.Event] = None
    classification: Any = None
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ExecutionEngine(Protocol):
    """执行引擎统一接口。

    实现者通常持有底层引擎对象（``ReActEngine`` / ``ComputerUseEngine`` /
    ``AAM`` 自身），并在 ``execute`` 中把适配后的结果包装成 ``DispatchResult``。
    """

    name: str
    priority: int

    def can_handle(self, text: str, *, intent: Optional[str], ctx: DispatchContext) -> bool: ...

    def execute(self, text: str, *, intent: Optional[str], ctx: DispatchContext) -> Optional[DispatchResult]: ...


class ExecutionRouter:
    """按 ``priority`` 升序遍历已注册引擎。

    使用模式：
    ```
    router = ExecutionRouter()
    router.register(ReActEngineAdapter(react_engine))
    router.register(ComputerUseEngineAdapter(computer_use, security, ai_client))
    router.register(GoalRunEngineAdapter(manager))
    result = router.try_dispatch("看新闻", intent=INTENT_EXECUTE, ctx=DispatchContext(...))
    ```
    """

    def __init__(self) -> None:
        self._engines: list[ExecutionEngine] = []

    def register(self, engine: ExecutionEngine) -> None:
        if not isinstance(engine, ExecutionEngine):
            raise TypeError(
                f"engine 必须实现 ExecutionEngine 协议: got {type(engine).__name__}"
            )
        self._engines.append(engine)
        # 稳定排序，保留同优先级的注册顺序
        self._engines.sort(key=lambda e: e.priority)

    def unregister(self, name: str) -> bool:
        before = len(self._engines)
        self._engines = [e for e in self._engines if e.name != name]
        return len(self._engines) < before

    @property
    def engines(self) -> list[ExecutionEngine]:
        return list(self._engines)

    def try_dispatch(
        self,
        text: str,
        *,
        intent: Optional[str] = None,
        ctx: Optional[DispatchContext] = None,
    ) -> Optional[DispatchResult]:
        """依次尝试每个引擎；返回第一个非 None 的 DispatchResult。"""
        ctx = ctx or DispatchContext()
        for engine in self._engines:
            try:
                if not engine.can_handle(text, intent=intent, ctx=ctx):
                    continue
            except Exception:
                logger.warning("ExecutionEngine.can_handle 抛异常: %s", engine.name, exc_info=True)
                continue

            try:
                result = engine.execute(text, intent=intent, ctx=ctx)
            except Exception:
                logger.warning("ExecutionEngine.execute 抛异常: %s", engine.name, exc_info=True)
                continue

            if result is not None:
                return result
        return None


# ═══════════════════════════════════════════════════════════════════
# 内置适配器：把现有 3 个引擎包装成 ExecutionEngine
# ═══════════════════════════════════════════════════════════════════


class ReActEngineAdapter:
    """ReAct 工具循环——dispatch 入口最先尝试。

    无论 intent 是否已分类都会跑：ReAct 自己判断"是否需要工具"，
    不需要时返回 None，让 router 走到下一个引擎。
    """

    name = "react"
    priority = 0

    def __init__(self, react_engine: Any) -> None:
        self._engine = react_engine

    def can_handle(self, text: str, *, intent: Optional[str], ctx: DispatchContext) -> bool:
        # intent 已分类为非 EXECUTE/None 时，ReAct 不再插手（避免被 chat 重复拦截）
        if intent not in (None, INTENT_EXECUTE):
            return False
        return self._engine is not None

    def execute(self, text: str, *, intent: Optional[str], ctx: DispatchContext) -> Optional[DispatchResult]:
        result_text = self._engine.process_message(text)
        if result_text is None:
            return None
        return DispatchResult.handled(
            intent=INTENT_CHAT,
            data={
                "intent": INTENT_CHAT,
                "react_reply": result_text,
                "classification": {
                    "intent": INTENT_CHAT,
                    "confidence": 0.9,
                    "reason": "react_engine",
                    "sub_intent": "",
                    "metadata": {},
                },
            },
        )


class ComputerUseEngineAdapter:
    """桌面自动化（关键字匹配触发，仅在 intent==EXECUTE 时尝试）。"""

    name = "computer_use"
    priority = 10

    def __init__(
        self,
        computer_use: Any,
        security: Any,
        ai_client: Any,
        keywords: frozenset[str],
        triggers: frozenset[str],
    ) -> None:
        self._engine = computer_use
        self._security = security
        self._ai_client = ai_client
        self._keywords = keywords
        self._triggers = triggers

    def can_handle(self, text: str, *, intent: Optional[str], ctx: DispatchContext) -> bool:
        if intent != INTENT_EXECUTE:
            return False
        lower = text.lower().strip()
        if any(kw in lower for kw in self._keywords):
            return True
        return any(trig in lower for trig in self._triggers)

    def execute(self, text: str, *, intent: Optional[str], ctx: DispatchContext) -> Optional[DispatchResult]:
        if self._ai_client is None:
            logger.warning("Computer Use requested but no AI client available")
            return DispatchResult.not_handled(
                intent=INTENT_EXECUTE,
                data={
                    "intent": INTENT_EXECUTE,
                    "goal": text,
                    "error": "AI client not configured — cannot run computer use",
                },
            )

        logger.info("Routing to Computer Use Agent: %.100s", text)

        # 预批准本次会话所有桌面动作
        approved = set(getattr(self._engine, "COMPUTER_USE_ACTIONS", ()))

        def _auth(step, _source="assistant.computer_use"):
            return self._security.authorize_step(step, source=_source, pre_approved=approved)

        result = self._engine.execute(
            text,
            authorize_callback=_auth,
            cancellation_event=ctx.cancellation_event,
        )
        result["intent"] = INTENT_EXECUTE
        result["goal"] = text
        return DispatchResult.handled(intent=INTENT_EXECUTE, data=result)


class GoalRunEngineAdapter:
    """目标规划 + 执行（默认 EXECUTE 兜底引擎）。"""

    name = "goal_run"
    priority = 20

    def __init__(self, run_goal: Callable[..., dict[str, object]]) -> None:
        # 持有 AAM.run_goal 的可调用引用，避免循环引用整个 manager
        self._run_goal = run_goal

    def can_handle(self, text: str, *, intent: Optional[str], ctx: DispatchContext) -> bool:
        return intent == INTENT_EXECUTE

    def execute(self, text: str, *, intent: Optional[str], ctx: DispatchContext) -> Optional[DispatchResult]:
        result = self._run_goal(
            text,
            source=ctx.source,
            delivery_channel=ctx.delivery_channel,
            chat_session_id=ctx.chat_session_id,
            cancellation_event=ctx.cancellation_event,
        )
        result["intent"] = INTENT_EXECUTE
        if ctx.classification is not None and hasattr(ctx.classification, "to_dict"):
            result["classification"] = ctx.classification.to_dict()
        return DispatchResult.handled(intent=INTENT_EXECUTE, data=result)


__all__ = [
    "DispatchContext",
    "ExecutionEngine",
    "ExecutionRouter",
    "ReActEngineAdapter",
    "ComputerUseEngineAdapter",
    "GoalRunEngineAdapter",
]

