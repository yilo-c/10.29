"""ActionBroker — 所有能力执行的统一入口闸门。

所有入口（UI、模型、插件、MCP、定时任务、浏览器桥接）
必须经过 ActionBroker.execute()，不允许直接调用底层执行器。

执行链路:
    execute(request)
      → CapabilityRegistry (能力存在且启用)
      → CapabilityInvocation (创建调用记录)
      → ActionGuard (风险/白名单/确认)
      → Executor (实际执行)
      → ExecutionReceipt (生成回执)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from services.capability_types import (
    CapabilityDescriptor,
    CapabilityInvocation,
    CapabilityRegistry,
    ConfirmationLevel,
    RiskLevel,
)

logger = logging.getLogger("siyi.assistant.action_broker")

# ── 七维-安C3(b)：确认必需集合 ──────────────────────────────────────
# first_time 此前全仓无强制点（七维报告 §1.1 A2/A3）：加入后 LLM/assistant/agent
# 来源的调用必须先经确认闭环（approved=True）才可执行；
# every_time/strong 原语义不变；auto（不在集合）仍自动执行。
CONFIRM_REQUIRED_LEVELS = frozenset(
    {
        ConfirmationLevel.FIRST_TIME.value,
        ConfirmationLevel.EVERY_TIME.value,
        ConfirmationLevel.STRONG.value,
    }
)


# ── 数据类型 ──────────────────────────────────────────────


@dataclass
class ActionRequest:
    """一次能力调用的请求。"""

    capability_id: str
    input: dict[str, Any] = field(default_factory=dict)
    source: str = "assistant"  # user / llm / assistant / plugin / cron / browser
    user_id: str = ""
    require_confirmation: bool = False
    approved: bool = False  # 已通过确认闭环人工授权（跳过 LLM 高危拦截）
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "input": dict(self.input),
            "source": self.source,
            "user_id": self.user_id,
            "require_confirmation": self.require_confirmation,
            "approved": self.approved,
            "metadata": dict(self.metadata),
        }


@dataclass
class ActionResult:
    """一次能力执行的结果。"""

    success: bool
    capability_id: str
    output: dict[str, Any] = field(default_factory=dict)
    receipt_id: str = ""
    error: str = ""
    risk_level: str = RiskLevel.READ.value
    confirmation: str = ""
    blocked: bool = False
    blocked_reason: str = ""
    invocation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "capability_id": self.capability_id,
            "output": dict(self.output),
            "receipt_id": self.receipt_id,
            "error": self.error,
            "risk_level": self.risk_level,
            "confirmation": self.confirmation,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "invocation_id": self.invocation_id,
        }


# ── ExecutionReceipt ──────────────────────────────────────


@dataclass
class ExecutionReceipt:
    """execution_receipt — 一次执行的标准回执。

    记录输入、输出、影响范围、风险、确认过程、失败原因和可回滚信息。
    每次执行（包括失败、拒绝、超时、用户取消）都必须生成回执。
    """

    receipt_id: str
    capability_id: str
    invocation_id: str
    status: str  # completed / failed / blocked / cancelled / timeout
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    risk_level: str = RiskLevel.READ.value
    confirmation: str = ConfirmationLevel.AUTO.value
    error: str = ""
    blocked_reason: str = ""
    rollback_policy: str = ""
    rollback_info: dict[str, Any] = field(default_factory=dict)
    audit_level: str = ""
    privacy_egress: str = ""
    source: str = ""
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "capability_id": self.capability_id,
            "invocation_id": self.invocation_id,
            "status": self.status,
            "input": dict(self.input),
            "output": dict(self.output),
            "risk_level": self.risk_level,
            "confirmation": self.confirmation,
            "error": self.error,
            "blocked_reason": self.blocked_reason,
            "rollback_policy": self.rollback_policy,
            "rollback_info": dict(self.rollback_info),
            "audit_level": self.audit_level,
            "privacy_egress": self.privacy_egress,
            "source": self.source,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


# ── ActionBroker ──────────────────────────────────────────


class ActionBroker:
    """统一执行闸门 — 所有能力执行的唯一入口。

    用法:
        broker = ActionBroker(
            capability_registry=registry,
            action_guard=guard,
            executor=my_executor_fn,
        )
        # 异步
        result = await broker.execute(ActionRequest(
            capability_id="file.read",
            input={"path": "/tmp/test.txt"},
            source="assistant",
        ))
        # 同步（自动检测 executor 是否为异步）
        result = broker.execute_sync(ActionRequest(
            capability_id="file.read",
            ...
        ))
    """

    def __init__(
        self,
        capability_registry: CapabilityRegistry,
        action_guard: Any,  # ActionGuard-compatible instance
        executor: Callable[..., Any] | None = None,  # (capability_id, input) -> dict (sync or async)
        receipt_store: Callable[[ExecutionReceipt], Any] | None = None,
        observability_service: Any = None,
    ):
        self._registry = capability_registry
        self._guard = action_guard
        self._executor = executor
        self._receipt_store = receipt_store
        self._observability = observability_service

    # ── 公开方法 ─────────────────────────────────────────

    async def execute(self, request: ActionRequest) -> ActionResult:
        """统一执行入口（异步）。"""
        started_at = _now()

        # 1-4. 前置校验（与同步路径共享逻辑）
        prep = self._prepare_and_validate(request, started_at)
        if prep is not None:
            return prep

        # 5. 执行
        if not self._executor:
            return self._build_blocked(
                request,
                "no_executor",
                "ActionBroker 未配置 executor",
                started_at=started_at,
            )

        capability = self._registry.get_capability(request.capability_id)
        if not capability:
            return self._build_blocked(
                request,
                "capability_not_found",
                f"能力 '{request.capability_id}' 未注册",
                started_at=started_at,
            )
        invocation = CapabilityInvocation(
            invocation_id=_id(),
            capability_id=request.capability_id,
            input=dict(request.input),
            source=request.source,
            risk_level=capability.risk_level,
            confirmation=capability.confirmation_level,
            started_at=started_at,
        )

        invocation.status = "running"
        try:
            output = self._executor(request.capability_id, request.input)
            # P0.3: 支持 sync + async executor — async executor 返回 coroutine
            if asyncio.iscoroutine(output):
                output = await output
            if not isinstance(output, dict):
                output = {"result": output}
            invocation.status = "completed"
            invocation.output = dict(output)
        except Exception as exc:
            invocation.status = "failed"
            invocation.error = str(exc)

        completed_at = _now()
        invocation.completed_at = completed_at

        # 6. 生成执行回执
        receipt_status = invocation.status  # completed / failed
        receipt = self._build_receipt(
            invocation=invocation,
            status=receipt_status,
            capability=capability,
            started_at=started_at,
            completed_at=completed_at,
        )
        self._store_receipt(receipt)

        return ActionResult(
            success=invocation.status == "completed",
            capability_id=request.capability_id,
            output=dict(invocation.output),
            receipt_id=receipt.receipt_id,
            invocation_id=invocation.invocation_id,
            error=invocation.error,
            risk_level=capability.risk_level,
            confirmation=capability.confirmation_level,
        )

    def execute_sync(self, request: ActionRequest) -> ActionResult:
        """同步版本的 execute。

        P0.3 修复：
        - 若当前线程已有运行中的 event loop（例如从 async handler 调用同步代码），
          ``asyncio.run`` 会抛 RuntimeError。此时降级走纯同步路径（要求 sync executor）。
        - 若无运行中的 loop，正常用 ``asyncio.run`` 跑 async 路径，支持 async executor。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 无运行中的 loop — 可安全使用 asyncio.run
            return asyncio.run(self.execute(request))

        # 当前线程已有运行中的 loop — 走纯同步路径
        return self._execute_pure_sync(request)

    def _execute_pure_sync(self, request: ActionRequest) -> ActionResult:
        """纯同步执行路径 — 在已有 event loop 的上下文中使用。

        要求 executor 是同步的。若 executor 返回 coroutine，无法在已有 loop
        中安全 await，返回 failed。
        """
        started_at = _now()

        prep = self._prepare_and_validate(request, started_at)
        if prep is not None:
            return prep

        if not self._executor:
            return self._build_blocked(
                request,
                "no_executor",
                "ActionBroker 未配置 executor",
                started_at=started_at,
            )

        capability = self._registry.get_capability(request.capability_id)
        if not capability:
            return self._build_blocked(
                request,
                "capability_not_found",
                f"能力 '{request.capability_id}' 未注册",
                started_at=started_at,
            )
        invocation = CapabilityInvocation(
            invocation_id=_id(),
            capability_id=request.capability_id,
            input=dict(request.input),
            source=request.source,
            risk_level=capability.risk_level,
            confirmation=capability.confirmation_level,
            started_at=started_at,
        )

        invocation.status = "running"
        try:
            output = self._executor(request.capability_id, request.input)
            if asyncio.iscoroutine(output):
                # 无法在已有 loop 中 await — 失败而非崩溃
                invocation.status = "failed"
                invocation.error = "async executor cannot be invoked via execute_sync from within a running event loop"
                logger.warning(
                    "execute_sync called with async executor inside running loop (capability=%s)",
                    request.capability_id,
                )
            else:
                if not isinstance(output, dict):
                    output = {"result": output}
                invocation.status = "completed"
                invocation.output = dict(output)
        except Exception as exc:
            invocation.status = "failed"
            invocation.error = str(exc)

        completed_at = _now()
        invocation.completed_at = completed_at

        receipt_status = invocation.status
        receipt = self._build_receipt(
            invocation=invocation,
            status=receipt_status,
            capability=capability,
            started_at=started_at,
            completed_at=completed_at,
        )
        self._store_receipt(receipt)

        return ActionResult(
            success=invocation.status == "completed",
            capability_id=request.capability_id,
            output=dict(invocation.output),
            receipt_id=receipt.receipt_id,
            invocation_id=invocation.invocation_id,
            error=invocation.error,
            risk_level=capability.risk_level,
            confirmation=capability.confirmation_level,
        )

    def _prepare_and_validate(
        self,
        request: ActionRequest,
        started_at: str,
    ) -> ActionResult | None:
        """前置校验：capability 检查 + invocation 创建 + guard + 确认。

        Returns:
            - 若被拦截：返回 ActionResult（调用方应直接返回）
            - 若通过：返回 None（调用方继续走执行路径，并用本方法重新构造 invocation）
        """
        # 1. 检查能力是否存在且启用
        capability = self._registry.get_capability(request.capability_id)
        if not capability:
            return self._build_blocked(
                request,
                "capability_not_found",
                f"能力 '{request.capability_id}' 未注册",
                started_at=started_at,
            )
        if not capability.enabled:
            return self._build_blocked(
                request,
                "capability_disabled",
                f"能力 '{request.capability_id}' 已禁用",
                started_at=started_at,
            )

        # 2. 创建调用记录（仅用于校验阶段；执行阶段会重新创建）
        invocation = CapabilityInvocation(
            invocation_id=_id(),
            capability_id=request.capability_id,
            input=dict(request.input),
            source=request.source,
            risk_level=capability.risk_level,
            confirmation=capability.confirmation_level,
            started_at=started_at,
        )

        # 3. ActionGuard 安全校验（approved=True 时跳过 LLM 高危拦截，
        # 因为该动作已经过确认闭环的人工授权）
        guard_result = self._guard.validate(
            action_id=request.capability_id,
            payload=request.input,
            source=request.source,
            approved=request.approved,
        )
        if not guard_result.allowed:
            receipt = self._build_receipt(
                invocation=invocation,
                status="blocked",
                blocked_reason=guard_result.reason,
                capability=capability,
                started_at=started_at,
            )
            self._store_receipt(receipt)
            return ActionResult(
                success=False,
                capability_id=request.capability_id,
                receipt_id=receipt.receipt_id,
                invocation_id=invocation.invocation_id,
                blocked=True,
                blocked_reason=guard_result.reason,
                risk_level=capability.risk_level,
            )

        # 4. 是否需确认（approved=True 表示已人工授权，不再重复要求确认）
        needs_confirmation = request.require_confirmation or capability.confirmation_level in CONFIRM_REQUIRED_LEVELS
        if needs_confirmation and not request.approved and request.source in ("llm", "assistant", "agent"):
            receipt = self._build_receipt(
                invocation=invocation,
                status="blocked",
                blocked_reason="requires_confirmation",
                capability=capability,
                started_at=started_at,
            )
            self._store_receipt(receipt)
            return ActionResult(
                success=False,
                capability_id=request.capability_id,
                receipt_id=receipt.receipt_id,
                invocation_id=invocation.invocation_id,
                blocked=True,
                blocked_reason="requires_confirmation",
                risk_level=capability.risk_level,
            )

        return None

    # ── 内部方法 ─────────────────────────────────────────

    def _build_blocked(
        self,
        request: ActionRequest,
        reason: str,
        message: str,
        *,
        started_at: str = "",
    ) -> ActionResult:
        return ActionResult(
            success=False,
            capability_id=request.capability_id,
            blocked=True,
            blocked_reason=reason,
            error=message,
        )

    def _build_receipt(
        self,
        *,
        invocation: CapabilityInvocation,
        status: str,
        capability: CapabilityDescriptor | None = None,
        blocked_reason: str = "",
        started_at: str = "",
        completed_at: str = "",
    ) -> ExecutionReceipt:
        return ExecutionReceipt(
            receipt_id=_id(),
            capability_id=invocation.capability_id,
            invocation_id=invocation.invocation_id,
            status=status,
            input=dict(invocation.input),
            output=dict(invocation.output),
            risk_level=invocation.risk_level,
            confirmation=invocation.confirmation,
            error=invocation.error,
            blocked_reason=blocked_reason,
            rollback_policy=capability.rollback_policy if capability else "",
            audit_level=capability.audit_level if capability else "",
            privacy_egress=capability.privacy_egress_policy if capability else "",
            source=invocation.source,
            started_at=started_at or _now(),
            completed_at=completed_at or _now(),
        )

    def _store_receipt(self, receipt: ExecutionReceipt) -> None:
        if self._receipt_store:
            try:
                self._receipt_store(receipt)
            except Exception as exc:
                logger.warning("Failed to store receipt %s: %s", receipt.receipt_id, exc)
        if self._observability:
            try:
                self._observability.track_event(
                    "action_broker.receipt",
                    payload={"receipt_id": receipt.receipt_id, "status": receipt.status},
                )
            except Exception as exc:
                logger.warning("Failed to track receipt event: %s", exc)


# ── 工具函数 ──────────────────────────────────────────────


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
