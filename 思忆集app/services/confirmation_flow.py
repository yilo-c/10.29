"""confirmation_flow — 确认请求的稳定层编排（审批闭环依赖倒置 / M2a 落地）。

背景
====
原审批闭环的编排硬依赖 ``assistant.butler_agent.resolve_confirmation``，违背内核
迁移边界——稳定区 services 不得反向依赖（import）assistant（不稳定内核层）。
本模块用依赖倒置解决：稳定区提供编排（resolve / create+emit）与执行 handler
注册表，由 assistant 层（butler）把“有上下文真实执行”逻辑经 ``register_execution_handler``
注入，**本模块不 import assistant**（内部只走 services/、backend/、app_runtime/
及核心基础设施 core/）。

公共能力
========
* ``create_and_emit_confirmation(...)``：稳定版发起确认（store.create + 上下文暂存
  + 发射 ``action.confirm_required``），供 react_executor 与 butler 的
  ``_request_confirmation`` 复用（行为与 butler 原实现等价）。
* ``resolve_confirmation(...)``：审批编排（store.resolve → 委托执行 handler →
  set_result → 发射 ``action.confirm_resolved``）。无上下文/未注册执行 handler 时
  仅翻转状态返回 ``{"approved": True, "note": "等待调用方（内核）重新下发动作"}``，
  不真正执行动作（与 butler 原“无上下文不执行”分支等价）。
* ``register_execution_handler(fn)``：模块级执行 handler 注册表（依赖倒置注入点）。

注册时机安全性
==============
条目带上下文 ⇒ butler 本进程存活 ⇒ 模块已加载 ⇒ handler 已注册；进程重启则上下文
本就丢失（_pending_contexts 在进程内），语义不变。
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Optional

from services.confirmation_store import compute_args_sha256, get_confirmation_store

logger = logging.getLogger("siyi.services.confirmation_flow")

_CONFIRM_TTL_SECONDS = 300.0

# ── 模块级执行 handler 注册表（依赖倒置注入点）────────────────────────────
# handler 签名 ``(confirmation_id, approved, reason) -> result_dict``（可为协程函数）。
# handler 内部自行 pop 暂存的执行上下文；无上下文时应返回
# ``{"approved": True, "note": "等待调用方（内核）重新下发动作"}``。
ExecutionHandler = Callable[[str, bool, Any], Any]

_execution_handlers: list[ExecutionHandler] = []
_context_storer: Optional[Callable[[str, Any], None]] = None


def register_execution_handler(fn: ExecutionHandler) -> None:
    """注册审批通过后的“真实执行”handler（依赖倒置注入点，butler 模块尾部调用）。"""
    _execution_handlers.append(fn)


def register_context_store(fn: Callable[[str, Any], None]) -> None:
    """注册执行上下文暂存回调（butler 注入：confirmation_id -> context 持久到进程内）。"""
    global _context_storer
    _context_storer = fn


def _emit_event(event_type: str, payload: dict[str, Any]) -> None:
    """经 EventBus 发布事件（核心基础设施，非 assistant 层），失败不阻断主流程。"""
    try:
        from core.events import AppEvent, event_bus

        event_bus.emit(AppEvent(event_type=event_type, payload=payload, source="confirmation_flow"))
    except Exception:
        pass


def create_and_emit_confirmation(
    context: Any,
    action_id: str,
    args: dict[str, Any],
    *,
    source: str = "butler",
    title: str = "",
    detail: str = "",
) -> dict[str, Any]:
    """稳定版发起确认：store.create + 上下文暂存 + 发射 ``action.confirm_required``。

    供 react_executor._request_confirmation 与 butler_agent._request_confirmation 复用，
    行为与 butler 原实现等价（pending 返回结构、事件 payload 均一致）。
    """
    store = get_confirmation_store()
    confirmation_id = store.create(
        action_id,
        dict(args or {}),
        source=source,
        title=title or action_id,
        detail=detail,
        ttl_s=_CONFIRM_TTL_SECONDS,
    )
    # 暂存执行上下文（依赖倒置：由注册方 butler 提供回调，本模块不 import assistant）
    if _context_storer is not None:
        _context_storer(confirmation_id, context)
    _emit_event(
        "action.confirm_required",
        {
            "confirmation_id": confirmation_id,
            "action_id": action_id,
            "args": dict(args or {}),
            "source": source,  # 前端据此区分：butler 审批批准后走 chat.resume 续跑
            "reason": f"{action_id} 属于需要确认的操作",
        },
    )
    return {
        "pending_confirmation": True,
        "confirmation_id": confirmation_id,
        "message": f"已向用户发起 {action_id} 的确认请求，等待用户批准。你可以继续完成其他步骤，或用 confirmation.status 查询结果。",
    }


async def _invoke_execution_handler(
    confirmation_id: str,
    approved: bool,
    reason: Any,
) -> Optional[dict[str, Any]]:
    """逐个调用已注册执行 handler，返回首个非 None 结果（同步/协程兼容）。"""
    for fn in _execution_handlers:
        try:
            ret = fn(confirmation_id, approved, reason)
            if inspect.isawaitable(ret):
                ret = await ret
            if ret is not None:
                return ret
        except Exception as exc:
            # M2.1 QUA-2：执行器异常如实上报（记 warning + 返回 error 结果），
            # 不再吞异常 continue 落入"等待重新下发"的 approved 兜底文案。
            logger.warning(
                "confirmation %s: execution handler %r raised: %s",
                confirmation_id,
                getattr(fn, "__name__", repr(fn)),
                exc,
                exc_info=True,
            )
            return {"error": str(exc)}
    return None


async def resolve_confirmation(
    confirmation_id: str,
    approved: bool,
    reason: str = "",
) -> dict[str, Any]:
    """审批闭环编排：状态翻转 → 委托执行 handler → 回填结果 → 发射事件。

    委托给已注册的 handler（approve/reject 都委托：approve 负责真实执行与无上下文
    note，reject 负责拒绝结果与 checkpoint 记录）。无 handler 时按缺省分支兜底：
    批准 → 仅翻转状态不执行动作；拒绝 → 直接 rejected。
    """
    store = get_confirmation_store()
    # M2.1 SEC-1（A3b）：批准必须绑定当初预览的参数指纹。先读条目取其 args_sha256
    # （缺省时按 args 复算），显式传给 store.resolve——不再走"缺省 None=免检"。
    preview = store.get(confirmation_id)
    bound_sha: Optional[str] = None
    if preview is not None:
        bound_sha = preview.get("args_sha256") or compute_args_sha256(preview.get("args"))
    entry = store.resolve(confirmation_id, approved, reason=reason or "", args_sha256=bound_sha)
    if entry is None:
        return {"success": False, "reason": "confirmation_not_found_or_expired"}
    action_id = entry["action_id"]
    result: dict[str, Any] = await _invoke_execution_handler(confirmation_id, approved, reason)
    if result is None:
        if approved:
            result = {"approved": True, "note": "等待调用方（内核）重新下发动作"}
        else:
            result = {"rejected": True}
    store.set_result(confirmation_id, result)
    _emit_event(
        "action.confirm_resolved",
        {
            "confirmation_id": confirmation_id,
            "action_id": action_id,
            "approved": approved,
            "reason": reason or "",
            "result": result,
        },
    )
    return {"success": True, "approved": approved, "reason": reason or "", "result": result}


def get_confirmation_status(confirmation_id: str) -> dict[str, Any]:
    """只读查询确认状态（稳定版，供 react_executor 复用；读覆盖 store.get）。"""
    entry = get_confirmation_store().get(confirmation_id)
    if entry is None:
        return {"confirmation_id": confirmation_id, "status": "expired_or_not_found"}
    out: dict[str, Any] = {
        "confirmation_id": confirmation_id,
        "action_id": entry["action_id"],
        "status": entry["status"],
    }
    if entry["status"] == "approved" and entry.get("result") is not None:
        out["result"] = entry["result"]
    return out


# ── DSH shell.run 两阶段确认（2026-09-09，方案 A）────────────────────────
# 背景：DSH 内核（react_executor）对 shell.run 只调用 create_and_emit_confirmation
# 返回 pending 占位给 LLM，本轮即结束，没有 butler 那样的 checkpoint 断点续跑。
# 修复：前端批准 → confirmation.resolve → 本文件 resolve_confirmation 委托本模块
# 注册的执行 handler → 在“批准时”真正执行 shell.run 并把结果 set_result；前端随后
# 调 chat.resume（DSH 网关退化为重新 submit）触发内核重发，react_executor 再次
# 收到 shell.run 时经 _consume_approved_shell 直接返回真实结果，不再二次确认，
# DSH 得以收尾输出——实现“批准即自动重发”闭环。
#
# 约束：本模块属稳定区，不 import assistant；capability_router / shell_domain
# 一律经 getattr 动态访问，不违反依赖倒置边界。

_shell_tasks: dict[str, dict[str, Any]] = {}  # confirmation_id -> {context,args}
_shell_consumed: set[str] = set()  # 已被重发复用的确认 id
_shell_handler_registered = False


def _execute_shell_approved(args: dict[str, Any], context: Any) -> dict[str, Any]:
    """批准后真正执行 shell.run（复用 capability_router 的 shell_domain）。"""
    router = getattr(context, "capability_router", None)
    shell_domain = getattr(router, "shell_domain", None) if router is not None else None
    if shell_domain is None:
        return {"error": "shell_domain 不可用，无法执行已批准的命令"}
    try:
        fn = getattr(shell_domain, "shell_run", None)
        if fn is None:
            return {"error": "shell_domain.shell_run 不可用"}
        return fn(dict(args or {}))
    except Exception as exc:
        logger.warning("approved shell.run execution failed: %s", exc, exc_info=True)
        return {"error": f"已批准命令执行失败: {exc}"}


def _ensure_shell_execution_handler() -> None:
    """注册一次性 DSH shell 执行 handler（幂等，仅注册一次）。"""
    global _shell_handler_registered
    if _shell_handler_registered:
        return

    def _shell_handler(confirmation_id: str, approved: bool, reason: Any) -> Optional[dict[str, Any]]:
        task = _shell_tasks.pop(confirmation_id, None)
        if task is None:
            return None  # 非 DSH shell 确认：交给其它已注册 handler / 默认兜底
        if not approved:
            return {"rejected": True, "reason": reason or ""}
        result = _execute_shell_approved(task.get("args", {}), task.get("context"))
        get_confirmation_store().set_result(confirmation_id, result)
        return result

    register_execution_handler(_shell_handler)
    _shell_handler_registered = True


def request_shell_confirmation(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    """react_executor 的 shell.run 入口：发起两阶段确认。

    返回 ``{pending_confirmation: True, confirmation_id, message}``（供 LLM 作工具
    答案）；批准时由已注册执行 handler 真实执行并回填 store。
    """
    store = get_confirmation_store()
    confirmation_id = store.create(
        "shell.run",
        dict(args or {}),
        source="mcp:kernel",
        title="执行系统命令",
        detail=str((args or {}).get("command", ""))[:200],
        ttl_s=_CONFIRM_TTL_SECONDS,
    )
    _shell_tasks[confirmation_id] = {"context": context, "args": dict(args or {})}
    _ensure_shell_execution_handler()
    _emit_event(
        "action.confirm_required",
        {
            "confirmation_id": confirmation_id,
            "action_id": "shell.run",
            "args": dict(args or {}),
            "source": "mcp:kernel",  # 前端据此走"批准即自动重发"分支
            "reason": "shell.run 属于需要确认的操作",
        },
    )
    return {
        "pending_confirmation": True,
        "confirmation_id": confirmation_id,
        "message": "已向用户发起 shell.run 的确认请求，等待用户批准。批准后会自动重发执行。",
    }


def consume_approved_shell(args: dict[str, Any]) -> Optional[dict[str, Any]]:
    """DSH 重发时复用已批准且已执行的 shell.run 结果（一次性消费）。

    匹配条件：action_id 为 shell.run、status 已 approved、含 result、命令一致、且
    未被消费过。命中则返回真实结果并标记已消费，避免后续再次二次确认。
    """
    store = get_confirmation_store()
    try:
        items = store.list()
    except Exception:
        return None
    wanted = dict(args or {})
    for entry in reversed(items):
        cid = str(entry.get("confirmation_id") or "")
        if entry.get("action_id") != "shell.run" or entry.get("status") != "approved":
            continue
        if entry.get("result") is None or cid in _shell_consumed:
            continue
        if dict(entry.get("args") or {}) == wanted:
            _shell_consumed.add(cid)
            return entry["result"]
    return None
