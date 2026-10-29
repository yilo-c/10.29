"""Butler 挂起任务 checkpoint 存取（P1-1 复用 CheckpointManager）。

挂起的 butler 任务（PydanticAI 单循环）在遇到 CONFIRM 工具时被中断，
把 message_history 等状态写入与 planner 路径共享的 ``assistant_checkpoints``
存储（CheckpointManager），由 ``chat.resume`` 恢复。

约定：
- ``task_type == "pydantic_agent"``（存于 context_snapshot）标识 butler 挂起任务，
  与 planner 步骤任务（无此键 / task_type=agent_task）区分。
- 恢复时只需 ``session_id``：scan_unfinished_tasks 返回非终端任务的完整
  checkpoint（含 context_snapshot），按其过滤。
- 多次 CONFIRM 工具可同时挂起：``confirmations`` 列表逐项记录，全部处理完
  （approved/rejected）后才允许 resume。
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

logger = logging.getLogger("siyi.assistant.butler_checkpoint")

TASK_TYPE = "pydantic_agent"
STATE_WAITING_APPROVAL = "waiting_approval"


def resolve_checkpoint_manager(context: Any) -> Any | None:
    """取与 planner 路径共享的 CheckpointManager；不可用时返回 None。

    解析优先级（与真实 DI 对齐）：
    1. autonomy.autonomous_assistant_manager.checkpoint_manager —— 真实挂载点
       （lifecycle_builder 把 CheckpointManager 挂在 AutonomousAssistantManager 上，
       与 planner 的 recover_task 共享同一 storage 实例）；
    2. task_manager.checkpoint_manager / task_manager._checkpoint_manager（兼容）；
    3. context.local_data_store（真实 AppContext）或 data_store（测试/兜底）新建。
    """
    try:
        autonomy = getattr(context, "automation", None)
        aam = getattr(autonomy, "autonomous_assistant_manager", None)
        cm = getattr(aam, "checkpoint_manager", None)
        if cm is not None:
            return cm
        tm = getattr(aam, "task_manager", None)
        cm = getattr(tm, "checkpoint_manager", None)
        if cm is None:
            cm = getattr(tm, "_checkpoint_manager", None)
        if cm is not None:
            return cm
    except Exception:
        logger.debug("resolve checkpoint_manager via autonomy failed", exc_info=True)

    try:
        ds = getattr(context, "local_data_store", None) or getattr(context, "data_store", None)
        if ds is not None:
            from assistant.checkpoint import CheckpointManager

            return CheckpointManager(data_store=ds)
    except Exception:
        logger.debug("resolve checkpoint_manager via data_store failed", exc_info=True)
    return None


def _serialize_messages(messages: list[Any]) -> str:
    """ModelMessage 列表 → base64 JSON 字符串（可存 JSON storage）。"""
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    raw = ModelMessagesTypeAdapter.dump_json(list(messages))
    return base64.b64encode(raw).decode("ascii")


def deserialize_messages(b64: str) -> list[Any]:
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    return ModelMessagesTypeAdapter.validate_json(base64.b64decode(b64))


# 兼容旧私有名
_deserialize_messages = deserialize_messages


def save_butler_checkpoint(
    context: Any,
    *,
    task_id: str,
    session_id: str,
    messages: list[Any],
    remaining_requests: int,
    confirmations: list[dict[str, Any]],
    title: str = "",
    pending_injections: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """挂起：保存（或更新）butler 任务 checkpoint。"""
    cm = resolve_checkpoint_manager(context)
    if cm is None:
        logger.warning("butler checkpoint save skipped: checkpoint_manager unavailable")
        return None
    snapshot = {
        "task_type": TASK_TYPE,
        "session_id": session_id,
        "message_history_b64": _serialize_messages(messages),
        "remaining_requests": max(0, int(remaining_requests)),
        "confirmations": list(confirmations),
        "pending_injections": list(pending_injections or []),
    }
    return cm.save_checkpoint(
        task_id,
        state=STATE_WAITING_APPROVAL,
        completed_steps=[],
        step_results=[],
        context_snapshot=snapshot,
        plan={"title": title or task_id},
    )


def _iter_butler_checkpoints(context: Any):
    """遍历所有非终端的 butler checkpoint（含 context_snapshot）。"""
    cm = resolve_checkpoint_manager(context)
    if cm is None:
        return
    for cp in cm.scan_unfinished_tasks() or []:
        cs = cp.get("context_snapshot") or {}
        if cs.get("task_type") == TASK_TYPE:
            yield cm, cp


def load_butler_checkpoint(context: Any, session_id: str) -> dict[str, Any] | None:
    """取该会话最新的挂起 butler checkpoint。"""
    for _cm, cp in _iter_butler_checkpoints(context):
        cs = cp.get("context_snapshot") or {}
        if cs.get("session_id") == session_id:
            return cp
    return None


def find_by_confirmation(context: Any, confirmation_id: str) -> dict[str, Any] | None:
    """按确认 ID 找挂起 checkpoint（resolve_confirmation 用）。"""
    for _cm, cp in _iter_butler_checkpoints(context):
        cs = cp.get("context_snapshot") or {}
        for c in cs.get("confirmations", []):
            if c.get("id") == confirmation_id:
                return cp
    return None


def record_confirmation_resolution(
    context: Any,
    confirmation_id: str,
    *,
    approved: bool,
    tool: str,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """记录一次人工确认结果到挂起 checkpoint（approved/rejected 都记录）。

    返回更新后的 checkpoint；找不到对应 checkpoint 时返回 None。
    """
    for cm, cp in _iter_butler_checkpoints(context):
        cs = dict(cp.get("context_snapshot") or {})
        confirmations = list(cs.get("confirmations", []))
        found = False
        for c in confirmations:
            if c.get("id") == confirmation_id:
                c["status"] = "approved" if approved else "rejected"
                found = True
                break
        if not found:
            continue
        injections = list(cs.get("pending_injections", []))
        injections.append({
            "tool": tool,
            "status": "approved" if approved else "rejected",
            "result": result,
        })
        cs["confirmations"] = confirmations
        cs["pending_injections"] = injections
        return save_butler_checkpoint(
            context,
            task_id=str(cp.get("task_id", "")),
            session_id=str(cs.get("session_id", "")),
            messages=_deserialize_messages(str(cs.get("message_history_b64", ""))),
            remaining_requests=int(cs.get("remaining_requests", 0)),
            confirmations=confirmations,
            title=str((cp.get("plan") or {}).get("title", "")),
            pending_injections=injections,
        )
    return None


def all_confirmations_resolved(cp: dict[str, Any]) -> bool:
    """挂起 checkpoint 的所有确认是否都已处理（approved/rejected）。"""
    cs = cp.get("context_snapshot") or {}
    confirmations = cs.get("confirmations", [])
    return all(bool(c.get("status")) for c in confirmations)


def apply_pending_injections(history: list[Any], cp: dict[str, Any]) -> list[Any]:
    """把已批准/拒绝的工具结果注入消息历史。

    按顺序把 history 最后一条 ModelRequest 中的 tool-return part 的 content
    替换为实际结果（顺序与 tool_calls 一致）。这样模型在续跑时看到的是
    真实结果而不是 "pending_confirmation" 占位文本（LangGraph interrupt 语义）。
    """
    cs = cp.get("context_snapshot") or {}
    injections = cs.get("pending_injections", [])
    if not injections:
        return history

    target = None
    for msg in reversed(history):
        if getattr(msg, "kind", "") == "request":
            target = msg
            break
    if target is None:
        return history

    idx = 0
    for part in target.parts:
        if getattr(part, "part_kind", "") != "tool-return":
            continue
        if idx >= len(injections):
            break
        inj = injections[idx]
        part.content = (
            f"用户已确认工具 {inj['tool']}（{'批准' if inj.get('status') == 'approved' else '拒绝'}）。"
            f"实际结果：{json.dumps(inj.get('result', {}), ensure_ascii=False, default=str)[:6000]}"
        )
        idx += 1
    return history
