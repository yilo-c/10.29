"""Sandbox (沙箱预览) JSON-RPC handlers — preview → approve/reject tool execution."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.sandbox_manager import get_request_connection, get_sandbox_manager
from app_runtime.app_context import AppContext
from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.backend.handlers.sandbox")


def register_sandbox(registry, context: AppContext) -> None:
    """Register sandbox preview/approve/reject JSON-RPC handlers.

    V2.1 批次2：approve/batch_approve 需要 registry 引用——kind="rpc" 的待审批
    条目（dispatch 层 R4 方法）批准后经 registry.dispatch(skip_approval=True) 重放执行。
    """

    async def _approve_with_registry(pending_id: str = "") -> dict[str, Any]:
        return await _handle_sandbox_approve(pending_id, registry=registry)

    async def _batch_approve_with_registry(pending_ids: list | None = None, **kwargs: Any) -> dict[str, Any]:
        return await _handle_sandbox_batch_approve(pending_ids, registry=registry)

    async def _preview_with_context(action_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """C-1 步骤 3：把 context 经闭包注入预览通路（B 套已无内联实现，
        预览也必须走 context.capability_router，不能再 make_react_executor(None)）。"""
        return await _handle_sandbox_preview(action_id, context=context, **kwargs)

    registry.register("sandbox.preview", _preview_with_context)
    registry.register("sandbox.action_probe", _handle_sandbox_action_probe)
    registry.register("sandbox.approve", _approve_with_registry)
    registry.register("sandbox.reject", _handle_sandbox_reject)
    registry.register("sandbox.list_pending", _handle_sandbox_list_pending)
    registry.register("sandbox.batch_approve", _batch_approve_with_registry)
    # AG-C1③：审批历史查询/清空（环形留痕跨重启可见，见 services/sandbox_manager.py）
    registry.register("sandbox.history", _handle_sandbox_history)
    registry.register("sandbox.history.clear", _handle_sandbox_history_clear)


async def _handle_sandbox_preview(action_id: str = "", *, context: Any = None, **kwargs: Any) -> dict[str, Any]:
    """Execute a tool action in dry_run mode and return a preview.

    Args:
        action_id: The tool action_id to preview (e.g. "file.write")
        context: AppContext——仅当 sandbox manager 尚未注入 tool executor 时
            用于现场构造（须携带 capability_router）
        **kwargs: Arguments to pass to the tool executor (including path, content, etc.)

    Returns:
        Preview result with diff/impact information and a pending_id for approval.
    """
    if not action_id:
        return {"error": "缺少 action_id 参数"}

    if action_id not in ("file.write", "file.copy"):
        return {"error": f"工具 '{action_id}' 不支持沙箱预览（仅支持 file.write 和 file.copy）"}

    mgr = get_sandbox_manager()
    executor = mgr._tool_executor
    if executor is None:
        # C-1 步骤 3 收缩：降级路径已删除。旧写法 `make_react_executor(None)` 依赖
        # B 套自带的第二份 file.* 内联实现（无 A 套白名单/env 门控），正是要收敛掉
        # 的影子执行体。现在只允许经挂载了 capability_router 的 context 构造唯一
        # 执行体；两者都拿不到就 fail-closed 返回明确错误（不再静默用降级实现）。
        from services.react_executor import make_react_executor, resolve_router

        if resolve_router(context) is None:
            logger.error("sandbox.preview(%s) 拒绝：tool executor 未注入且 context 无 capability_router", action_id)
            return {
                "error": "沙箱预览不可用：sandbox tool executor 未注入，且当前 context 未挂载 capability_router（请先完成 set_tool_executor 注入）"
            }
        executor = make_react_executor(context)

    try:
        dry_args = dict(kwargs)
        dry_args["dry_run"] = True
        # V2.1 批次2：executor 为同步调用（文件/进程 IO），包 to_thread 防阻塞事件循环
        return await asyncio.to_thread(executor, action_id, dry_args)
    except Exception as exc:
        logger.error("sandbox.preview(%s) 失败: %s", action_id, exc)
        return {"error": f"沙箱预览失败: {exc}"}


async def _handle_sandbox_action_probe(action_id: str = "", command: str = "", **kwargs: Any) -> dict[str, Any]:
    """A-P0：动作可达性/风险**受控探针**——只判定、继不执行任何 handler、零副作用。

    供 actuation 工具普查 / 规划器预检 / CI 结构断言用。返回：是否在册、LLM 是否可见、
    风险层级、默认(on-risk)策略下是否需审批、file.* 是否支持 dry_run、shell 命令是否含不安全 token。
    """
    if not action_id:
        return {"error": "缺少 action_id 参数"}
    from services.react_tool_catalog import REACT_TOOL_CATALOG, SAFE_TOOL_IDS_BASE

    entry = next((e for e in REACT_TOOL_CATALOG if e["action_id"] == action_id), None)
    in_catalog = entry is not None
    llm_visible = action_id in SAFE_TOOL_IDS_BASE

    high_risk = write_action = False
    try:
        from core.action_guard import _HIGH_RISK_ACTIONS, _WRITE_ACTIONS  # 同库内只读判定

        high_risk = action_id in _HIGH_RISK_ACTIONS
        write_action = action_id in _WRITE_ACTIONS
    except Exception:  # noqa: BLE001
        logger.debug("action_probe: ActionGuard 层级集不可用", exc_info=True)

    tier = "high_risk" if high_risk else ("write" if write_action else ("read_only" if in_catalog else "unknown"))

    has_unsafe_shell = False
    if action_id == "shell.run" and command:
        try:
            from core.action_guard import SHELL_UNSAFE_TOKENS

            has_unsafe_shell = any(tok in command for tok in SHELL_UNSAFE_TOKENS)
        except Exception:  # noqa: BLE001
            has_unsafe_shell = False
            logger.debug("action_probe: SHELL_UNSAFE_TOKENS 不可用，回退 false", exc_info=True)

    return {
        "action_id": action_id,
        "in_catalog": in_catalog,
        "llm_visible": llm_visible,
        "tier": tier,
        "high_risk": high_risk,
        "write_action": write_action,
        "approval_required_default_policy": high_risk,  # on-risk 缺省下仅高风险需人工确认
        "dry_run_supported": action_id in ("file.write", "file.copy"),
        "has_payload_schema": bool(entry and entry.get("payload_schema")),
        "shell_unsafe_tokens_present": has_unsafe_shell,
    }


def _interpret_action_result(result: Any) -> tuple[bool, str]:
    """判定 RPC 重放结果是否表示「动作成功」。返回 (ok, 失败原因)。

    为什么需要（2026-10-15 T1 实测）：``sandbox.approve`` 的 rpc 分支原实现把
    「dispatch 未抛异常」一律当成功——``result=False`` / ``{"success": False}`` /
    ``{"error": ...}`` 也回报 ``success=True``。实测 ``tasks.delete`` 重放返回
    False（删除未发生），审批层仍报成功，用户点「批准」看到成功而数据还在。

    判定保守（宁可漏报失败，不误报成功为失败）：
    - ``False`` → 失败
    - ``{"success": False}`` → 失败（取其 error/message 摘要）
    - ``{"error": <非空>}`` 且未显式 ``success=True`` → 失败
    - ``None`` / ``True`` / 其他 → 成功（多数破坏性动作以 None 表示"已完成"）
    """
    if result is False:
        return False, "action returned False"
    if isinstance(result, dict):
        if result.get("success") is False:
            return False, str(result.get("error") or result.get("message") or "action returned success=False")
        if result.get("error") and result.get("success") is not True:
            return False, str(result["error"])
    return True, ""


async def _handle_sandbox_approve(pending_id: str = "", *, registry=None) -> dict[str, Any]:
    """Approve a pending sandbox operation and execute it.

    Args:
        pending_id: The pending_id returned by sandbox.preview
        registry: MethodRegistry（仅 kind="rpc" 条目需要，用于批准后重放执行）

    Returns:
        Execution result of the approved operation.
    """
    if not pending_id:
        return {"error": "缺少 pending_id 参数"}

    mgr = get_sandbox_manager()
    pending = mgr.get(pending_id)
    if not pending:
        return {"error": f"pending_id 不存在或已过期: {pending_id}"}
    if pending["status"] != "pending":
        return {"error": f"操作 {pending_id} 状态为 {pending['status']}，无法批准"}

    # M2.1 G1：审批动作必须来自可信 UI 持有者，且不得由发起该待审批操作的同一连接
    # 自我批准。匿名/注入 WS 帧（绕过握手的连接）非 trusted，同连接自批一律拒绝；
    # 经握手认证的 UI 连接（桌面/持 token 手机端）代表真实用户点击，放行；内部/异步
    # 入队（无连接上下文）不触发本拦截，向后兼容既有审批闭环。
    #
    # ⚠ 裁决 1（authorization-model-adjudication-2026-09-17，勿当 bug 修）：
    #   trusted 连接「同连接自批」= 合法设计，**不是**"人工介入在 WS 面不构成强制"
    #   的缺陷。威胁模型是防 AI 自主执行破坏性动作（kernel-contract §4.5 F-1），
    #   不是防用户本人：token 在手即机器已失守，任何"防用户自己"的跨连接二次校验
    #   都是伪安全且伤害多窗产品形态。UI 弹卡 + 人点批准就是人类介入点，同连接
    #   自批等价于主窗自批。P-C 实测该链含 args_sha256 预览绑定、审计留痕、幂等
    #   拒绝，作为"防 AI 自主"有效。后人请勿把它改成跨连接校验。
    conn_id, trusted = get_request_connection()
    requester = pending.get("requester_conn")
    if requester and conn_id and requester == conn_id and not trusted:
        logger.warning("sandbox.approve(%s) 被拒：请求方连接自我批准", pending_id)
        return {"success": False, "pending_id": pending_id, "error": "审批不得由发起该操作的同一连接自我批准（需可信 UI 持有者通道）"}

    # V2.1 批次2：dispatch 层 R4 方法（kind="rpc"）批准后由 registry 重放执行
    # （skip_approval=True 跳过二次审批拦截）；工具类（kind="tool"）走 mgr.approve。
    # AJ-3：本分支此前 pop 后直接重放、不经 mgr.approve()，是唯一"批准执行却无审计
    # 留痕"的路径（reject/timeout 都有）——最高危动作反而不可追溯。与 mgr.approve
    # 的 tool 路径同构补 record_history：成功 event="approved"，执行失败或无法
    # 重放 event="failed"（含错误摘要），kind 字段区分 rpc/tool。
    if pending.get("kind") == "rpc":
        action_id = pending["action_id"]
        args = dict(pending.get("args") or {})
        mgr._pending.pop(pending_id, None)
        pending["status"] = "approved"
        if registry is None:
            pending["status"] = "failed"
            mgr.record_history("failed", pending, status="failed", error="rpc 审批缺少 registry")
            return {"success": False, "pending_id": pending_id, "error": "rpc 审批缺少 registry"}
        try:
            result = await registry.dispatch(action_id, args, skip_approval=True)
            # 2026-10-15（T1 实测修复）：原实现无条件 success=True——把"dispatch 未抛
            # 异常"当成"动作做成了"。实测 tasks.delete 重放后返回 False（删除没发生），
            # approve 仍报 success=True + result=False，用户看到"批准成功"而任务还在。
            # 现在按重放结果判定：明确失败 → 如实回报失败并记 failed 审计。
            ok, reason = _interpret_action_result(result)
            if not ok:
                logger.warning("sandbox approve 重放结果失败: %s(%s) -> %s", action_id, pending_id, reason)
                pending["status"] = "failed"
                mgr.record_history("failed", pending, status="failed", error=f"post-approval execution failed: {reason}")
                return {
                    "success": False,
                    "pending_id": pending_id,
                    "action_id": action_id,
                    "error": f"批准后执行未成功：{reason}",
                    "result": result,
                }
            mgr.record_history("approved", pending, status="approved")
            try:
                event_bus.emit(
                    AppEvent(
                        "sandbox.approved",
                        payload={"pending_id": pending_id, "action_id": action_id, "success": True},
                    )
                )
            except Exception:
                logger.debug("failed to emit sandbox.approved event", exc_info=True)
            return {"success": True, "pending_id": pending_id, "action_id": action_id, "result": result}
        except Exception as exc:
            logger.warning("sandbox approve 执行失败: %s", exc, exc_info=True)
            pending["status"] = "failed"
            mgr.record_history("failed", pending, status="failed", error=str(exc))
            return {"success": False, "pending_id": pending_id, "error": str(exc)}

    result = await asyncio.to_thread(mgr.approve, pending_id)
    return result


async def _handle_sandbox_batch_approve(pending_ids: list | None = None, *, registry=None, **kwargs: Any) -> dict[str, Any]:
    """P1-5：批量批准待审批条目（并发任务审批不逐条点）。

    Args:
        pending_ids: 待批准的 pending_id 列表
        registry: MethodRegistry（kind="rpc" 条目批准后重放执行需要）

    Returns:
        汇总结果：approved/total + 逐条 results。
    """
    pids = pending_ids if isinstance(pending_ids, list) else []
    pids = [str(p) for p in pids if p]
    if not pids:
        return {"success": False, "error": "缺少 pending_ids 参数"}
    if len(pids) > 100:
        return {"success": False, "error": "单次批量审批上限 100 条"}
    mgr = get_sandbox_manager()
    results = []
    for pid in pids:
        pending = mgr.get(pid)
        if pending and pending.get("kind") == "rpc":
            results.append(await _handle_sandbox_approve(pid, registry=registry))
        else:
            # B-04: 与单条路径（:116）对齐 —— mgr.approve 对 tool 条目含
            # _tool_executor 文件/进程 IO，同步调用会阻塞事件循环 N×executor 时长
            results.append(await asyncio.to_thread(mgr.approve, pid))
    approved = sum(1 for r in results if r.get("success"))
    return {
        "success": True,
        "approved": approved,
        "total": len(pids),
        "results": results,
    }


async def _handle_sandbox_reject(pending_id: str = "") -> dict[str, Any]:
    """Reject and discard a pending sandbox operation.

    Args:
        pending_id: The pending_id to reject.

    Returns:
        Confirmation of rejection.
    """
    if not pending_id:
        return {"error": "缺少 pending_id 参数"}

    mgr = get_sandbox_manager()
    result = mgr.reject(pending_id)
    return result


async def _handle_sandbox_history(limit: int = 50, **kwargs: Any) -> dict[str, Any]:
    """AG-C1③：查询审批历史（新的在前）。

    Args:
        limit: 返回条数上限（默认 50，最大 SANDBOX_HISTORY_CAP）

    Returns:
        {"items": [...]}，items 为审批处置摘要（不含 args/preview 原文）。
    """
    mgr = get_sandbox_manager()
    if mgr is None:
        return {"error": "SandboxManager 不可用"}
    try:
        limit_int = int(limit if limit is not None else 50)
    except (TypeError, ValueError):
        return {"error": "limit 必须为整数"}
    return {"items": mgr.list_history(limit=limit_int)}


async def _handle_sandbox_history_clear(**kwargs: Any) -> dict[str, Any]:
    """AG-C1③：清空审批历史（进程内环形 + kv 落盘键）。"""
    mgr = get_sandbox_manager()
    if mgr is None:
        return {"error": "SandboxManager 不可用"}
    mgr.clear_history()
    return {"cleared": True}


async def _handle_sandbox_list_pending(**kwargs: Any) -> list[dict[str, Any]]:
    """List all pending sandbox operations awaiting approval.

    Returns:
        List of pending operation previews.
    """
    mgr = get_sandbox_manager()
    pending = mgr.list_pending()

    # Return clean summaries (strip full content for large diffs)
    result: list[dict[str, Any]] = []
    for p in pending:
        summary = {
            "pending_id": p["pending_id"],
            "action_id": p["action_id"],
            "status": p["status"],
            "created_at": p["created_at"],
        }
        preview = p.get("preview", {})
        if preview.get("diff"):
            # Truncate long diffs in listing
            diff = preview["diff"]
            preview["diff"] = diff[:500] + "\n..." if len(diff) > 500 else diff
        summary["preview"] = preview
        result.append(summary)

    return result
