"""PydanticAI 管家路径 — chat.send 默认执行引擎（P3 收敛）。

单 agent 循环：消息永远携带工具定义，模型不需要工具时第一轮直接流式回复
（等价纯聊天），需要时自动进入工具循环。工具进度通过 tool_progress chunk
推给前端，最终回复流式输出。

任何初始化失败（pydantic_ai 未安装 / ai_client 缺失 / agent 构建失败）
都静默返回空，调用方回退 simple_stream。

M2 断点续跑（butler 任务生命周期）：
- 工具结果含 ``pending_confirmation`` 时视为挂起点：保存 checkpoint
  （message_history + 剩余预算 + 确认列表）并中断循环，发 ``task.waiting_approval``
  事件；资源立即释放。
- ``run_pydantic_agent_resume`` 供 ``chat.resume`` 调用：读 checkpoint →
  注入已批准/拒绝结果 → 从断点继续流式运行。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from backend.handlers.chat_paths.helpers import emit_graded_error

logger = logging.getLogger("siyi.chat_paths.pydantic_agent")


async def _safe_chunk(send_chunk: Any, text: str, chunk_type: str | None = None) -> None:
    """兼容新旧 send_chunk 签名（部分调用点为位置参数 only）。"""
    if send_chunk is None:
        return
    try:
        if chunk_type is not None:
            await send_chunk(text, chunk_type=chunk_type)
        else:
            await send_chunk(text)
    except TypeError:
        await send_chunk(text)


def _build_message_history(msgs: list[dict[str, Any]]) -> list[Any]:
    """把 build_messages 产出的 OpenAI 风格历史转为 PydanticAI 消息历史。

    只保留 user/assistant/system 文本消息；system 消息并入 system prompt 语义
    （PydanticAI 以 SystemPromptPart 表达）。
    """
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        SystemPromptPart,
        TextPart,
        UserPromptPart,
    )

    history: list[Any] = []
    for m in msgs or []:
        role = m.get("role", "")
        content = m.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "system":
            history.append(ModelRequest(parts=[SystemPromptPart(content=content)]))
        elif role == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif role == "assistant":
            history.append(ModelResponse(parts=[TextPart(content=content)]))
        # tool / 其他角色忽略（历史里不应出现）
    # 最后一条是本轮用户消息，由 run_stream_events(user_prompt=...) 单独传入
    while history and isinstance(history[-1], ModelRequest) and any(getattr(p, "part_kind", "") == "user-prompt" for p in history[-1].parts):
        history.pop()
    return history


def _extract_confirmation_id(content: str) -> str:
    """从 pending_confirmation 工具结果文本中解析 confirmation_id。"""
    if not content:
        return ""
    text = content.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            return str(data.get("confirmation_id") or "")
        except (ValueError, TypeError):
            pass
    for token in text.split(","):
        token = token.strip()
        if token.startswith("confirmation_id"):
            return token.split(":", 1)[-1].strip().strip("\"'")
    return ""


async def _run_agent_loop(
    context: Any,
    agent: Any,
    *,
    user_prompt: str | None,
    message_history: list[Any],
    params: dict[str, Any],
    send_chunk: Any,
    trace_task_id: str,
    session_id: str,
    title: str,
    request_limit: int | None = None,
    control: Any = None,
) -> AsyncIterator[str]:
    """共享的 PydanticAI 事件循环：工具进度 + 文本流 + 挂起检测。

    request_limit：本次运行的模型请求预算；None 时用 MAX_MODEL_REQUESTS，
    resume 恢复挂起任务时传 checkpoint 中的剩余预算。

    control：``ChatStreamControl``（可为 None）。chat.cancel 置位后，本循环在下一个
    事件边界 break 出 ``async with agent.run_stream_events(...)``——退出该上下文管理器
    即关闭底层 provider 流；终态落 cancelled 而非 completed。

    产出最终文本 chunk；挂起或异常时发对应状态 chunk/事件。
    """
    from pydantic_ai.messages import (
        FunctionToolCallEvent,
        FunctionToolResultEvent,
        PartDeltaEvent,
        TextPartDelta,
    )
    from pydantic_ai.usage import UsageLimits

    from backend.handlers.chat_paths._exec_step import emit_exec_step

    from assistant.butler_agent import MAX_MODEL_REQUESTS
    from assistant.butler_checkpoint import save_butler_checkpoint

    budget = request_limit if request_limit is not None else MAX_MODEL_REQUESTS
    yielded_any = False
    suspended = False
    cancelled = False
    latest_plan: dict[str, Any] | None = None  # B-P1b：本轮 update_plan 的最新计划快照
    try:
        async with agent.run_stream_events(
            user_prompt=user_prompt,
            message_history=message_history,
            deps=context,
            usage_limits=UsageLimits(request_limit=budget),
        ) as events:
            async for event in events:
                if control is not None and control.cancelled:
                    cancelled = True
                    break
                if isinstance(event, FunctionToolCallEvent):
                    part = event.part
                    emit_exec_step(
                        {
                            "task_id": trace_task_id,
                            "step_type": "tool_call",
                            "tool": str(part.tool_name or ""),
                            "summary": str(part.args)[:200],
                        }
                    )
                    await _safe_chunk(
                        send_chunk,
                        json.dumps(
                            {"tool": part.tool_name, "args": part.args, "status": "running"},
                            ensure_ascii=False,
                            default=str,
                        ),
                        chunk_type="tool_progress",
                    )
                elif isinstance(event, FunctionToolResultEvent):
                    part = event.part
                    # pydantic-ai 2.x: 真正的工具结果在 part.content（ToolReturnPart），
                    # event.content 是结果之后随 UserPromptPart 发给模型的附加内容，
                    # 普通文本工具恒为 None。旧代码误读 event.content 导致前端显示 "None"。
                    result_value = getattr(part, "content", None)
                    result_text = str(result_value or "")
                    result_status = "done" if part.__class__.__name__ != "RetryPromptPart" else "error"
                    emit_exec_step(
                        {
                            "task_id": trace_task_id,
                            "step_type": "tool_result",
                            "tool": str(part.tool_name or ""),
                            "summary": result_text[:200],
                            "status": "step" if result_status == "done" else "running",
                        }
                    )
                    await _safe_chunk(
                        send_chunk,
                        json.dumps(
                            {
                                "tool": part.tool_name,
                                "result": result_text[:400],
                                "status": result_status,
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                        chunk_type="tool_progress",
                    )
                    if str(getattr(part, "tool_name", "")) == "update_plan":
                        try:
                            latest_plan = json.loads(result_text)
                        except (ValueError, TypeError):
                            latest_plan = None
                    # ★ M2 挂起检测：CONFIRM 工具结果 → 保存 checkpoint 并中断循环
                    if '"pending_confirmation"' in result_text:
                        cid = _extract_confirmation_id(result_text)
                        try:
                            from pydantic_ai.messages import ModelRequest, ToolReturnPart

                            messages = list(events.all_messages())
                            # PydanticAI 发 FunctionToolResultEvent 时，tool-return 尚未写入
                            # message_history（事件先于状态落盘）。手动补齐挂起点的工具结果，
                            # 保证断点快照是完整的 [..., ModelResponse(tool-call),
                            # ModelRequest(tool-return)]——否则 resume 时 PydanticAI 会认为
                            # 末尾工具调用未完成而重执行（再次挂起，形成死循环）。
                            part = getattr(event, "part", None)
                            if part is not None and getattr(part, "part_kind", "") == "tool-return":
                                messages.append(
                                    ModelRequest(
                                        parts=[
                                            ToolReturnPart(
                                                tool_call_id=getattr(part, "tool_call_id", "") or "",
                                                tool_name=getattr(part, "tool_name", "") or "",
                                                content=getattr(part, "content", "") or "",
                                            )
                                        ]
                                    )
                                )
                            used = int(getattr(events.usage, "requests", 0) or 0)
                        except Exception:
                            logger.warning("butler suspend: failed to snapshot run state", exc_info=True)
                            break
                        confirmations = [{"id": cid, "tool": str(part.tool_name or ""), "status": None}]
                        saved = save_butler_checkpoint(
                            context,
                            task_id=trace_task_id,
                            session_id=session_id,
                            messages=messages,
                            remaining_requests=MAX_MODEL_REQUESTS - used,
                            confirmations=confirmations,
                            title=title,
                        )
                        suspended = True
                        emit_exec_step({"task_id": trace_task_id, "status": "waiting"})
                        from core.events import AppEvent, event_bus

                        try:
                            event_bus.emit(
                                AppEvent(
                                    event_type="task.waiting_approval",
                                    source="butler_agent",
                                    payload={
                                        "session_id": session_id,
                                        "task_id": trace_task_id,
                                        "confirmation_id": cid,
                                        "tool": str(part.tool_name or ""),
                                        "checkpoint_saved": saved is not None,
                                    },
                                )
                            )
                        except Exception:
                            logger.debug("task.waiting_approval emit failed", exc_info=True)
                        break
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    delta = event.delta.content_delta or ""
                    if delta:
                        yielded_any = True
                        if control is not None:
                            control.note_delta(delta)
                        await _safe_chunk(send_chunk, delta)
                        yield delta  # type: ignore[misc]  # generator delegation
        if suspended:
            pass
        elif cancelled:
            # ★ 会话终态落 cancelled，不再谎报 completed。
            #   退出 async with 即关闭 provider 流；终态 notice 由 dispatcher 统一发。
            if control is not None:
                control.request_cancel("user_cancelled")
            emit_exec_step({"task_id": trace_task_id, "status": "cancelled"})
        else:
            emit_exec_step({"task_id": trace_task_id, "status": "completed"})
            note = _plan_incompletion_note(latest_plan)
            if note:
                # B-P1b v1“不假成功”：计划未全部完成时如实告知，**不自动重跑**（避免二次执行副作用）
                await _safe_chunk(send_chunk, note)
                yield note  # type: ignore[misc]
    except asyncio.CancelledError:
        # 硬取消（宽限期兜底 / ws 断连）：只补一行观测，随后原样上抛让任务收尾
        if control is not None:
            control.request_cancel("task_cancelled")
        emit_exec_step({"task_id": trace_task_id, "status": "cancelled"})
        raise
    except Exception as exc:
        logger.warning("pydantic agent path failed: %s", exc, exc_info=True)
        emit_exec_step(
            {
                "task_id": trace_task_id,
                "status": "failed",
                "error": str(exc)[:300],
            }
        )
        payload = await emit_graded_error(
            send_chunk,
            exc,
            control=control,
            extra_technical=f"task_id={trace_task_id}",
            emit_chunks=yielded_any,
        )
        if payload.get("error_kind") == "budget_exhausted":
            # U0 实测（2026-09-19）：12 步预算耗尽曾静默落 unknown_error 并在
            # 无输出时降级 simple_stream——无工具路径重试同一任务必然再失败。
            # 预算耗尽必须就地终态：补发正式分型 error 帧（emit_graded_error
            # 在 emit_chunks=False 时既不发帧也不落状态，这里为其例外补齐），
            # 已有部分输出时追加明确的中止标注。
            if not yielded_any:
                payload = await emit_graded_error(
                    send_chunk,
                    exc,
                    control=control,
                    extra_technical=f"task_id={trace_task_id}",
                    emit_chunks=True,
                )
            else:
                err = "\n\n[管家步数预算耗尽，已中止；以上为已完成的部分结果]"
                await _safe_chunk(send_chunk, err)
                yield err  # type: ignore[misc]
            return
        if yielded_any:
            # 已有部分输出：追加错误说明，避免静默断流
            err = "\n\n[管家执行中断，已输出部分结果]"
            await _safe_chunk(send_chunk, err)
            yield err  # type: ignore[misc]
        else:
            # 尚无任何输出：不发 chunk，让 dispatcher 回退 simple_stream（既有降级链），
            # 终态由兜底路径给出，避免「报错了又好像没报错」的误导。
            logger.info("pydantic path failed before any output, will degrade: %s", payload["technical"])
    return


def _plan_incompletion_note(latest_plan: dict[str, Any] | None) -> str | None:
    """本轮用过 update_plan 且步骤未全部完成 → 返回如实提示文本；否则 None（默认路径零影响）。"""
    if not latest_plan:
        return None
    total = int(latest_plan.get("total", 0) or 0)
    done = int(latest_plan.get("done", 0) or 0)
    if total == 0 or done >= total:
        return None
    nxt = latest_plan.get("next")
    tail = f"，仍在进行：{nxt}" if nxt else ""
    return f"\n\n[计划未完成：{done}/{total} 步已完成{tail}]"


async def run_pydantic_agent_path(
    context,
    message,
    params,
    msgs,
    send_chunk,
    control: Any = None,
) -> AsyncIterator[str]:
    """PydanticAI 管家执行路径。产出最终回复 chunk；工具进度走 tool_progress。"""
    if control is not None and control.cancelled:
        return
    try:
        from assistant.butler_agent import get_butler_agent  # noqa: F401 — 可用性探测
    except ImportError:
        logger.debug("butler_agent module unavailable")
        return

    agent = get_butler_agent(context)
    if agent is None:
        return

    history = _build_message_history(msgs[:-1] if msgs else [])
    session_id = str(params.get("session_id") or "")

    # P1-2：执行观测 —— 以会话维度开 trace run（工具调用步骤逐步记录）
    from backend.handlers.chat_paths._exec_step import emit_exec_step, make_task_id

    trace_task_id = make_task_id(session_id, prefix="chat")
    emit_exec_step(
        {
            "task_id": trace_task_id,
            "task_type": "chat_tool",
            "title": str(message or "")[:80],
            "source": "butler",
            "status": "started",
        }
    )

    async for chunk in _run_agent_loop(
        context,
        agent,
        user_prompt=message,
        message_history=history,
        params=params,
        send_chunk=send_chunk,
        trace_task_id=trace_task_id,
        session_id=session_id,
        title=str(message or "")[:80],
        control=control,
    ):
        yield chunk


async def run_pydantic_agent_resume(context, params, send_chunk, control: Any = None) -> AsyncIterator[str]:
    """chat.resume：从 checkpoint 恢复挂起的 butler 任务（流式续跑）。

    挂起 → 批准/拒绝 → resume 的完整闭环。若无挂起任务则发状态 chunk 说明。
    """
    from assistant.butler_agent import get_butler_agent
    from assistant.butler_checkpoint import (
        all_confirmations_resolved,
        apply_pending_injections,
        load_butler_checkpoint,
    )

    agent = get_butler_agent(context)
    if agent is None:
        return

    session_id = str(params.get("session_id") or "")
    cp = load_butler_checkpoint(context, session_id)
    if cp is None:
        await _safe_chunk(
            send_chunk,
            json.dumps({"resumed": False, "reason": "no_pending_task"}, ensure_ascii=False),
            chunk_type="chat_resume_status",
        )
        return

    if not all_confirmations_resolved(cp):
        await _safe_chunk(
            send_chunk,
            json.dumps({"resumed": False, "reason": "confirmation_pending"}, ensure_ascii=False),
            chunk_type="chat_resume_status",
        )
        return

    cs = cp.get("context_snapshot") or {}
    from assistant.butler_checkpoint import deserialize_messages

    try:
        history = deserialize_messages(str(cs.get("message_history_b64", "")))
    except Exception as exc:
        logger.warning("chat.resume: message_history deserialize failed: %s", exc)
        await _safe_chunk(
            send_chunk,
            json.dumps({"resumed": False, "reason": "checkpoint_invalid"}, ensure_ascii=False),
            chunk_type="chat_resume_status",
        )
        return

    # 注入已批准/拒绝的工具结果（替换 pending 占位文本）
    history = apply_pending_injections(history, cp)
    trace_task_id = str(cp.get("task_id", ""))
    title = str((cp.get("plan") or {}).get("title", ""))

    # 用剩余预算恢复运行
    remaining = max(1, int(cs.get("remaining_requests", 0)))
    from backend.handlers.chat_paths._exec_step import emit_exec_step

    emit_exec_step({"task_id": trace_task_id, "task_type": "chat_tool", "status": "resumed"})

    async for chunk in _run_agent_loop(
        context,
        agent,
        user_prompt=None,
        message_history=history,
        params=params,
        send_chunk=send_chunk,
        trace_task_id=trace_task_id,
        session_id=session_id,
        title=title,
        request_limit=remaining,
        control=control,
    ):
        yield chunk
