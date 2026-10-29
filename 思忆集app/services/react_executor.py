"""ReAct 工具执行器 — **薄适配层**（审计波次② C-1 步骤 3 收缩后的形态）。

历史上本模块是旧 ReAct 引擎的工具执行体（1100+ 行内联实现），与 A 套
``assistant/capability_router.py`` 构成**两个执行体**（C-1 病灶）：同样的
file.read/write/copy、notes.*/tasks.* 各自实现一遍，安全策略只在其一生效。

收缩后这里不再有任何动作实现：全部经 A 套路由分派
（``router.dispatch_sync`` → ActionBroker → 路由内部 handler 表 → 动态工具；
本模块不碰路由的私有成员）。本模块只保留
三件 **sandbox 确认闭环专属** 的事：

1. 破坏性操作审批预队门（``notes.delete`` / ``notes.batch_delete`` / ``tasks.delete``
   —— 实现见 ``services.tool_exec.enforce_destructive_approval``）；
2. ``shell.run`` 的两阶段确认转发：A 套 ``shell_domain.shell_run`` 是**裸执行**，
   把 shell.run 下沉给它等于绕过确认闭环（consume_approved_shell /
   request_shell_confirmation），故这一条保留在本适配层，不下沉；
3. 返回信封归一：A 套 ``{"success", "reason"}`` → 本模块历史消费者依赖的
   ``{"error": ...}``（失败态补 error 键，成功态原样透传，A 套 handler 自身的
   ``{"error"/"result"}`` 形状不受影响）。

入参 ``context`` 需要提供 ``capability_router``（AppContext 既有字段）。
``context=None`` 的兜底挂载已随 ``backend/handlers/_sandbox.py`` 一起改为
**fail-closed**：无路由即拒绝执行并给出明确错误，不再自带一份降级实现——
那正是本波要收敛掉的第二执行体。

同步性说明：本工厂返回的仍是**同步**闭包，挂载侧
（``sandbox.set_tool_executor`` / ``_sandbox.py`` 的 ``asyncio.to_thread``）签名不变；
故走 ``dispatch_sync`` 而非 ``await dispatch``（worker 线程内无 running loop）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app_runtime.app_context import AppContext

from services.tool_exec import (
    DESTRUCTIVE_ACTIONS,
    enforce_destructive_approval,
    is_in_approval_context,
)

logger = logging.getLogger("siyi.services.react_executor")

# 分派来源标记：本执行器只服务 sandbox 工具通路（审批队列的 kind="tool" 条目
# 与文件类 dry_run 预览）。注意 **不要** 改成 "assistant"/"llm"：
# ActionGuard.is_llm_elevated 只对 ("llm","agent","assistant") 生效，而本通路里
# file.write / app.launch 这类 elevated 动作的出路是这里的审批预队门与
# shell.run 两阶段确认（不是 broker 的终态拦截），换标记会把已经能跑通的工具
# 调用变成 broker 直拒。审批后的执行流额外带 approved=True 进 guard 留痕。
DISPATCH_SOURCE = "sandbox"


def resolve_router(context: Any) -> Any:
    """从 context 取 A 套路由；取不到返回 None（调用侧 fail-closed）。

    兼容两条历史挂载：``context.capability_router`` 与
    ``context.autonomous_assistant_manager.router``（同 butler_agent
    ._resolve_capability_router 的兜底顺序）。
    """
    if context is None:
        return None
    router = getattr(context, "capability_router", None)
    if router is None or not callable(getattr(router, "dispatch_sync", None)):
        manager = getattr(context, "autonomous_assistant_manager", None)
        router = getattr(manager, "router", None) if manager is not None else None
    if router is None or not callable(getattr(router, "dispatch_sync", None)):
        return None
    return router


def _to_executor_envelope(action_id: str, raw: Any) -> dict[str, Any]:
    """A 套返回 → 本模块历史信封：失败态补 ``error`` 键，其余原样透传。

    A 套能力的失败信封是 ``{"success": False, "reason": ...}``（``app.launch`` 用
    ``{"launched": False, ...}``），而 ReAct / 沙箱侧一直按 ``"error" in result``
    判定失败（且把该文案回注给模型自我纠正），故只做最小叠加、不改写任何既有键，
    避免第三种返回形状。
    """
    if not isinstance(raw, dict):
        return {"result": raw}
    out = dict(raw)
    failed = out.get("success") is False or out.get("launched") is False
    if failed and "error" not in out:
        out["error"] = str(out.get("reason") or out.get("message") or f"工具 {action_id} 执行失败")
    return out


def make_react_executor(context: AppContext):
    """构造 ReAct 引擎的工具执行器（薄适配：审批门 + A 套分派）。

    返回一个同步可调用 ``(action_id, args) -> dict``。
    """

    def _execute(action_id: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = dict(args) if isinstance(args, dict) else {}

            # ── 破坏性操作沙箱确认（非审批执行流先入待审批队列）──
            held = enforce_destructive_approval(action_id, payload, context)
            if held is not None:
                return held

            # ── shell.run：危险操作必须先发起用户确认（与 butler_agent
            # CONFIRM_TOOLS 策略一致），不得直接执行 ──
            # 两阶段确认（2026-09-09，方案 A）：批准时由 confirmation_flow 的
            # 执行 handler 真正执行命令并回填结果；DSH 内核被前端 chat.resume
            # 重发后，先试复用已批准结果，避免二次确认、让内核得以收尾。
            if action_id == "shell.run":
                from services.confirmation_flow import (
                    consume_approved_shell,
                    request_shell_confirmation,
                )

                reused = consume_approved_shell(payload)
                if reused is not None:
                    return reused
                return request_shell_confirmation(context, payload)

            # ── 其余全部动作：单一执行体 = A 套 capability_router ──
            router = resolve_router(context)
            if router is None:
                logger.error(
                    "react executor 无法分派 %s：context 未挂载 capability_router（B 套已无内联实现）",
                    action_id,
                )
                return {
                    "error": "capability_router 不可用：工具执行体已收敛到 A 套路由，"
                    "未挂载路由的调用方必须先注入 context.capability_router"
                }
            raw = router.dispatch_sync(
                action_id,
                payload,
                source=DISPATCH_SOURCE,
                approved=is_in_approval_context(),
            )
            return _to_executor_envelope(action_id, raw)

        except Exception as exc:
            logger.exception("react executor raised for %s", action_id)
            return {"error": f"工具执行异常: {exc}"}

    return _execute


__all__ = ["make_react_executor", "resolve_router", "DESTRUCTIVE_ACTIONS", "DISPATCH_SOURCE"]