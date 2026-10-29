"""Chat, memory, and tags JSON-RPC handlers."""

from __future__ import annotations

import asyncio
import json
import time
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from services.react_tool_catalog import (
    parse_inline_model,
    react_catalog_to_openai_tools,
)

# A1 重构：chat_send 的各执行路径（aggregate / multi_agent / pydantic_agent /
# gateway / simple_stream）已拆分到 backend.handlers.chat_paths 包中，
# 本文件只保留 dispatcher 与其他 chat/memory/tags handler。
from backend.handlers.chat_paths import (
    _chat_send_aggregate,
    build_messages,
    run_gateway_path,
    run_gateway_path_progressive,
    run_multi_agent_path,
    run_simple_stream_path,
    run_pydantic_agent_path,
    run_gateway_resume,
)
from backend.handlers.chat_paths.gateway_path import (
    _use_gateway_route,
    _get_gateway,
    _safe_chunk,
)
from backend.handlers.chat_paths.helpers import (
    active_chat_streams,
    cancel_chat_streams,
    emit_cancelled_notice,
    emit_graded_error,
    register_chat_stream,
    unregister_chat_stream,
)
from backend.handlers._list_guard import cap_list, clamp_limit

# P0-12: 旧私有别名 _react_catalog_to_openai_tools 仍被
# tests/test_chat_memory_tool_required.py 跨模块导入，必须保留
# （审计 §9-7 曾判“零消费”，实测有消费者，不成立）。
_react_catalog_to_openai_tools = react_catalog_to_openai_tools

logger = logging.getLogger("siyi.backend.handlers.chat_memory")

# 降级链分级状态提示（D5 / 契约 §2.5）：显式降级发 chat notice，隐式降级仅徽章。
_KERNEL_NOTICE_THROTTLE: dict[str, float] = {}
_KERNEL_NOTICE_WINDOW_S = 300.0


async def _emit_kernel_degraded_notice(send_chunk, reason: str) -> None:
    """用户显式要求内核（params.kernel）却降级时，向 chat 内发一行状态提示。

    隐式降级（默认路由降级）不调用本函数，仅由 StatusBar 徽章 + kernel.status
    体现，避免每次请求刷屏（D5 "auto-degrade with notices" 的两级语义）。
    """
    now = time.monotonic()
    if _KERNEL_NOTICE_THROTTLE.get(reason, 0.0) + _KERNEL_NOTICE_WINDOW_S > now:
        return  # 同原因 5 分钟内只发一次
    _KERNEL_NOTICE_THROTTLE[reason] = now
    payload = json.dumps(
        {"event": "kernel_degraded", "from": "gateway", "to": "legacy", "reason": reason},
        ensure_ascii=False,
    )
    try:
        await _safe_chunk(send_chunk, payload, chunk_type="notice")
    except Exception:
        logger.debug("emit kernel_degraded notice failed", exc_info=True)


# ── chat.send aggregation helper ─────────────────────────
#
# A1 重构：`_chat_send_aggregate` 已迁出至
# `backend.handlers.chat_paths.aggregate_path`，本文件顶部已 re-import。
# 保留符号以便 `chat_send` dispatcher 直接调用。


# ── Tool helpers ────────────────────────────────────────────────────────────
# 注：_react_catalog_to_openai_tools / _build_tool_name_map 已迁移至
# services.react_tool_catalog（参见本文件顶部 import 与别名）。


def _parse_tool_call_delta(chunk_str: str) -> dict[str, Any] | None:
    """Parse a streaming tool‑call delta from the model output.

    DeepSeek and OpenAI‑compatible providers may deliver tool_calls
    as inline JSON chunks during stream.
    """
    try:
        data = json.loads(chunk_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    # Only extract tool_call shapes
    tc = data.get("tool_calls") or data.get("tool_call")
    if tc:
        return tc if isinstance(tc, dict) else (tc[0] if isinstance(tc, list) and tc else None)
    return None


# ── chat session id 处理 ────────────────────────────────────────────────────
#
# 历史 bug：chat.history 用 int(session_id) 走仓储，chat.save_messages 用
# 字符串 key 写到独立 chat_conversations.json，两者永远查不到对方的数据。
# 现在统一走 chat_session_repository（仓储要求 int sid）。前端历史会话 id
# 既可能是数字（"42"）也可能是 UUID（"abc-123"），后者要做一次稳定 hash
# 映射到正整数，避免触发 Python int() 异常。


def _coerce_session_id(raw: Any) -> int | None:
    """把任意 session_id 表示统一为正整数 sid。

    规则：
    - int / 数字字符串 → int(...)（必须 > 0）
    - 非数字字符串 → 用 sha1 取前 8 hex 转 int 作为稳定 sid
      （绝对值 < 2^32，足够 chat_sessions.json 索引用）
    - 无效输入 → None
    """
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if not isinstance(raw, str):
        try:
            return _coerce_session_id(str(raw))
        except Exception:
            logger.exception("会话ID类型转换失败")
            return None
    s = raw.strip()
    if not s:
        return None
    try:
        sid = int(s)
        return sid if sid > 0 else None
    except ValueError:
        # 非数字 session_id（如前端用 UUID 或 timestamp-string）→ 稳定哈希
        import hashlib

        h = hashlib.sha1(s.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        return int(h, 16) or 1


def _client_id_for_raw(raw: Any) -> str | None:
    """若 raw 是非空非数字字符串则视为前端 client_id（UUID 等），原样返回；否则 None。"""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        int(s)
        return None
    except ValueError:
        return s


def _resolve_session_id(repo, raw: Any) -> tuple[int | None, str | None]:
    """解析前端 session_id → (内部 int sid, 原始 client_id)。

    优先在 sessions_index 中匹配 client_id，命中则直接返回索引里的 sid，
    避免对同一 UUID 重复 hash 时出现碰撞。未命中时退回到 _coerce_session_id。
    """
    sid = _coerce_session_id(raw)
    client_id = _client_id_for_raw(raw)
    if client_id and repo is not None and hasattr(repo, "_load_sessions_index"):
        try:
            for entry in repo._load_sessions_index() or []:
                if entry.get("client_id") == client_id:
                    return entry.get("id"), client_id
        except Exception:
            logger.debug("resolve_session_id: index lookup failed", exc_info=True)
    return sid, client_id


def _ensure_session_with_id(repo, sid: int, title: str, client_id: str | None = None) -> None:
    """确保索引中存在 id=sid 的会话；若不存在则插入。

    LocalChatSessionRepository.create_session 会自动分配新 id，无法用前端给定的
    sid。这里直接操作索引文件层，保证 sid 一致性。

    client_id：若提供（前端原始 UUID 字符串），会写入索引；后续 list_sessions
    返回时优先用 client_id 作为对外 id，保持前后端 session_id 一致。
    """
    try:
        # 修复 B11: 防御性检查 — 确保仓储有所需的私有方法
        if not (hasattr(repo, "_load_sessions_index") and hasattr(repo, "_save_sessions_index")):
            logger.warning(
                "ensure_session_with_id: repo %s 缺少 _load_sessions_index/_save_sessions_index 方法，回退到 create_session（sid 一致性无法保证）",
                type(repo).__name__,
            )
            if hasattr(repo, "create_session"):
                repo.create_session(title)
            return

        sessions = repo._load_sessions_index()
        for s in sessions:
            if s.get("id") == sid:
                # 已存在，但若之前没记 client_id 且这次带了，则补写一次
                if client_id and not s.get("client_id"):
                    s["client_id"] = client_id
                    repo._save_sessions_index(sessions)
                    if hasattr(repo, "_save_meta"):
                        repo._save_meta(sid, dict(s))
                return
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "id": sid,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        }
        if client_id:
            entry["client_id"] = client_id
        sessions.append(entry)
        repo._save_sessions_index(sessions)
        if hasattr(repo, "_save_meta"):
            repo._save_meta(sid, dict(entry))
    except Exception:
        logger.exception("ensure_session_with_id failed for sid=%s", sid)


_LEGACY_MIGRATION_DONE: set[str] = set()


def _migrate_legacy_chat_conversations(context, repo) -> None:
    """一次性迁移旧 chat_conversations.json → 新 chat_session_repository。

    旧版 chat.save_messages 写到 personal_note_repository.data_store 的
    chat_conversations.json 文件（key 是任意字符串，messages 内联）。
    本函数：
    1. 读取旧文件
    2. 对每个会话 _coerce_session_id 计算 int sid + sync_messages 写入新仓储
    3. 把旧文件改名为 chat_conversations.legacy.json 保留备份
    重复调用幂等（用 id(context) 标记已完成）。
    """
    # Stable key: use data dir path + "chat_conversations" to avoid id() reuse bugs
    data_dir = getattr(getattr(context, "personal_note_repository", None), "data_dir", "") or ""
    key = f"{data_dir}:chat_conversations"
    if key in _LEGACY_MIGRATION_DONE:
        return
    try:
        note_repo = getattr(context, "personal_note_repository", None)
        store = getattr(note_repo, "data_store", None) if note_repo else None
        if store is None:
            _LEGACY_MIGRATION_DONE.add(key)
            return
        legacy = store.load_json("chat_conversations", {})
        if not legacy or not isinstance(legacy, dict):
            _LEGACY_MIGRATION_DONE.add(key)
            return
        migrated = 0
        for raw_sid, data in legacy.items():
            sid = _coerce_session_id(raw_sid)
            if sid is None:
                continue
            if not isinstance(data, dict):
                continue
            title = data.get("title") or "已迁移会话"
            messages = data.get("messages") or []
            try:
                _ensure_session_with_id(repo, sid, title)
                repo.sync_messages(sid, messages)
                migrated += 1
            except Exception:
                logger.exception("迁移旧会话失败: raw_sid=%s sid=%s", raw_sid, sid)
        if migrated > 0:
            logger.info("已从 chat_conversations.json 迁移 %d 个旧会话到仓储", migrated)
            # 保留备份而非删除，避免迁移异常时数据无法恢复
            try:
                store.save_json("chat_conversations.legacy", legacy)
                store.save_json("chat_conversations", {})
            except Exception:
                logger.exception("旧 chat_conversations 备份/清空失败")
    finally:
        _LEGACY_MIGRATION_DONE.add(key)


def register_chat_memory(registry, context) -> None:
    """Register chat, memory, and tags-related JSON-RPC methods."""

    # ── chat.send (streaming, single-provider; supports non-stream aggregation) ──

    async def chat_send(
        send_chunk: Any = None,
        **params: Any,
    ) -> AsyncIterator[str]:
        message = params.get("message", "")
        if not message:
            # 空消息时发送 error chunk 让前端能区分并提示用户
            if send_chunk is not None:
                await send_chunk("消息不能为空", chunk_type="error")
            return

        # P1.8: 为本次 chat.send 生成 trace_id，贯穿 agent / ai / tool 调用链
        from backend.agent_context_vars import new_trace_id, set_trace_id, reset_trace_id, get_trace_id

        _trace_token = set_trace_id(new_trace_id())
        logger.info("chat.send trace_id=%s message=%r", get_trace_id(), message[:80])
        # QUA-13：把本次流登记进在途表，chat.cancel 才能把取消真正传播到 provider 流。
        control = register_chat_stream(params.get("session_id"))
        cancelled = False
        try:
            async for chunk in _chat_send_inner(
                context,
                message,
                params,
                send_chunk,
                control=control,
            ):
                if control.cancelled:
                    # 取消后不再向前端转发残余 delta；继续排空让各路径按自身取消逻辑
                    # 收尾（break/GeneratorExit 会把下游生成器吊在 join 上阻塞事件循环）。
                    cancelled = True
                    continue
                yield chunk
            if control.cancelled:
                cancelled = True
            if cancelled:
                await emit_cancelled_notice(send_chunk, control)
        except Exception as exc:
            logger.exception("chat.send failed")
            # V1-3：dispatcher 层不再把异常留给 ws 层的 "Internal error"，
            # 统一按分型上送（友好文案 + 技术细节 notice）。CancelledError 属
            # BaseException，不在此拦截，取消语义原样上抛。
            await emit_graded_error(send_chunk, exc, control=control, extra_technical="chat.send")
        finally:
            unregister_chat_stream(control)
            reset_trace_id(_trace_token)

    async def _chat_send_inner(
        context,
        message: str,
        params: dict[str, Any],
        send_chunk: Any,
        control: Any = None,
    ) -> AsyncIterator[str]:
        """chat.send 实际业务逻辑（在 trace_id 上下文内执行）。"""

        def _stop_requested() -> bool:
            return bool(control is not None and control.cancelled)

        # V1② 首反馈 fail-fast：AI 完全未配置时立即发 not_configured 分型
        # （实测未配置时走完整回退链 8s 零帧），不进任何执行路径。
        from backend.handlers.chat_paths.helpers import preflight_ai_or_emit

        if not await preflight_ai_or_emit(context, send_chunk, control=control):
            return

        parsed_msg, inline_provider, inline_model = parse_inline_model(message)
        preferred_provider = inline_provider or params.get("provider")
        preferred_model = inline_model or params.get("model")
        message = parsed_msg

        # ── Task #9 对话附件（2026-09-18）：文本内联 / 图片经 vision 转描述 ──
        # 必须在所有分支（aggregation / multi_agent / gateway / butler / simple）
        # 之前并入 message —— gateway 直接吃原始 message，晚注入会漏路径。
        attachments = params.get("attachments")
        if attachments:
            from backend.handlers.chat_paths.attachments import (
                enrich_message_with_attachments,
            )

            try:
                message = await enrich_message_with_attachments(context, message, attachments)
            except Exception as exc:
                logger.warning("attachment enrichment failed, continuing without: %s", exc, exc_info=True)

        aggregation = params.get("aggregation")
        stream_mode = params.get("stream", True)

        # ── aggregation 分支（支持流式与非流式）──────────────────
        if aggregation:
            async for chunk in _chat_send_aggregate(
                context,
                message,
                params,
                aggregation,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
                send_chunk=send_chunk,
                stream=bool(stream_mode),
            ):
                if _stop_requested():
                    return
                yield chunk
            return

        # ── multi_agent 分支（遗留路径，仅显式 mode="multi_agent" 触发）──
        explicit_multi = params.get("mode") == "multi_agent" or params.get("use_multi_agent") is True
        if explicit_multi and hasattr(context, "multi_agent_orchestrator"):
            # multi_agent 路径不支持用户指定的 provider/model
            if preferred_provider or preferred_model:
                logger.warning(
                    "multi_agent path ignores user-selected provider/model: provider=%s model=%s — orchestrator uses default AI client chain",
                    preferred_provider,
                    preferred_model,
                )
            multi_yielded = False
            async for chunk in run_multi_agent_path(context, message, params, send_chunk):
                multi_yielded = True
                yield chunk
                if _stop_requested():
                    return
            if multi_yielded:
                return
            # multi_agent 返回空 summary 或失败 → 回退到后续路径

        # ── 网关路径（Flip 已回翻，缺省走壳内管家）──
        # 四级路由：L1 params.kernel 强制 / L2 chat_route=gateway 显式 /
        # L3 chat_route=auto 分层（复杂→内核、简单→壳内）/ L4 缺省 legacy。
        # 网关不可用或空响应时返回 False，自然落到下方默认路径。
        # 必须在 build_messages 之前（网关直接吃原始 message，不复用遗留增强）。
        if run_gateway_path is not None and _use_gateway_route(context, params, message):
            # P0（2026-08-16）：渐进式管道 -- 内核长任务（DSH 56s+）期间
            # 逐事件 emit + 心跳 yield，持续重置 dispatch_stream 空闲计时器
            # （60s），修复"帮我打开vs code"超时事故。
            gw_handled = False
            gw_degrade_reason: str | None = None
            async for signal in run_gateway_path_progressive(context, message, params):
                if _stop_requested():
                    # QUA-13：chat.cancel 命中内核长任务 —— 立即停止逐事件下发与心跳，
                    # 不再进入壳内降级链（降级只会再点一次模型、继续烧 token）。
                    return
                sig = signal[0]
                if sig == "emit":
                    _, text, ctype = signal
                    await _safe_chunk(send_chunk, text, chunk_type=ctype)
                    gw_handled = True
                    yield ""  # 产出内容，重置空闲计时器
                elif sig == "heartbeat":
                    yield ""  # 内核仍在执行：仅重置计时器，不发送内容
                elif sig == "handled":
                    gw_handled = signal[1]
                    # 渐进式路径在致命内核错误回落时携带原因（如 kernel_error）
                    if len(signal) > 2 and signal[2]:
                        gw_degrade_reason = str(signal[2])
            if gw_handled:
                return
            if _stop_requested():
                return
            # 网关未产出 → 继续落到默认路径（降级）
            # 分级状态提示（D5 / 契约 §2.5）：
            # - 显式降级（用户主动 params.kernel 指定内核却落空）→ chat 内必发 notice，知情权优先
            # - 隐式降级（L3 默认路由降级）→ 不发 chat notice，仅 StatusBar 徽章 + kernel.status degraded
            if params.get("kernel"):
                reason = gw_degrade_reason or ("dsh_missing" if _get_gateway(context, params) is None else "empty_response")
                await _emit_kernel_degraded_notice(send_chunk, reason)

        # ── 构建消息列表（web search + context compression + anchor + persona）──
        if _stop_requested():
            return
        msgs = await build_messages(context, message, params)

        # B5（2026-08-26）：注入「对话记忆锚点」system 消息（时间/天气/相关记忆/画像/偏好）。
        # 仅在锚点启用且构建出非空文本时追加一条 system 消息；否则跳过，不影响主链路。
        # 追加到默认路径与 simple_stream 路径共用的 msgs，覆盖非 legacy 分支。
        try:
            from backend.handlers._chat_context_anchor import build_anchor_async

            anchor_text = await build_anchor_async(context, message)
            if anchor_text:
                msgs = list(msgs) + [{"role": "system", "content": anchor_text}]
        except Exception as exc:
            logger.warning("chat anchor build failed, skipping: %s", exc, exc_info=True)
            # N-F（2026-09-17）：debug→warning。锚点构建失败会让对话静默缺失
            # 时间/天气/记忆注入（用户困惑"怎么不记得上下文"），此前 debug 级
            # 不可见。降级行为不变（跳过锚点、不阻断主链路）。

        # ── 默认路径：PydanticAI 管家（单 agent 循环，永远携带工具）──
        # 无关键词门槛：由模型自行决定是否调用工具；纯聊天第一轮直接
        # 流式回复。不可用（pydantic_ai 未安装 / ai_client 缺失）或异常时
        # 回退 simple_stream。
        try:
            pa_yielded = False
            async for chunk in run_pydantic_agent_path(
                context,
                message,
                params,
                msgs,
                send_chunk,
                control=control,
            ):
                pa_yielded = True
                if _stop_requested():
                    continue  # 取消后丢弃残余 delta，等管家路径自行收尾
                yield chunk
            if pa_yielded:
                return
        except Exception:
            logger.exception("pydantic agent path failed, falling back to simple stream")

        if _stop_requested():
            # 管家路径未产出但已被取消：绝不继续降级到 simple_stream（那会另起一次模型请求）
            return

        # ── simple_stream 分支（最终保底，无工具调用）──────────
        async for chunk in run_simple_stream_path(
            context,
            msgs,
            send_chunk,
            preferred_provider,
            preferred_model,
            control=control,
        ):
            if _stop_requested():
                continue  # 同上：不转发，但要把流排空到底，避免线程侧挂起
            yield chunk

    registry.register("chat.send", chat_send)

    # ── chat.cancel（QUA-13 / V1-3：真取消）────────────────────────
    # 前端 stopChatStream（desk-notes/packages/desktop/src/components/chat/useChatStream.ts）
    # 会 abort 本地 AbortController 并 call('chat.cancel', {})。旧实现只回一句确认，
    # 后端 provider 流照跑，继续产 token —— 现改为真正传播取消：
    #   1) 置位在途流的 cancel_event（threading.Event，后台线程在 chunk 边界自查并
    #      close() provider 生成器 → 断开底层 HTTP 流）；
    #   2) 宽限期（2s）内未自行退出则 asyncio.Task.cancel() 硬兜底（远端 stall 时
    #      等不到 chunk 边界）；
    #   3) 终态落 cancelled（exec.step + notice + control.status），已产出的部分
    #      由 ai.client.base 的用量包装器如实记账。
    # 前端不带参数 ⇒ 缺省取消全部在途 chat 流（本机单用户语义）；带 session_id /
    # stream_id 时只定向取消。幂等：无在途流时返回 cancelled=True、streams=[]。
    async def chat_cancel(
        session_id: Any = "",
        stream_id: Any = "",
        reason: Any = "user_cancelled",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """取消在途 chat 流式输出（真正传播到 provider 流任务）。"""
        streams = cancel_chat_streams(
            stream_id=str(stream_id) if stream_id else None,
            session_id=str(session_id) if session_id else None,
            reason=str(reason or "user_cancelled"),
        )
        active = [ctl.summary() for ctl in active_chat_streams()]
        return {
            "cancelled": True,
            "canceled_streams": streams,
            "still_active": active,
            "note": "provider stream cancel propagated server-side" if streams else "no in-flight chat stream",
        }

    registry.register("chat.cancel", chat_cancel)

    # ── chat.resume（M2 断点续跑：从 checkpoint 恢复挂起的 butler 任务）──
    # 前端批准确认后调用：confirmation.resolve → chat.resume(session_id)。
    # 复用 chat.send 的流式通道，前端视角为同一对话继续输出。

    async def chat_resume(
        send_chunk: Any = None,
        **params: Any,
    ) -> AsyncIterator[str]:
        session_id = params.get("session_id", "")
        if not session_id:
            if send_chunk is not None:
                await send_chunk("缺少 session_id", chunk_type="error")
            return

        from backend.agent_context_vars import new_trace_id, set_trace_id, reset_trace_id, get_trace_id

        _trace_token = set_trace_id(new_trace_id())
        logger.info("chat.resume trace_id=%s session_id=%r", get_trace_id(), session_id)
        # QUA-13：续跑同样是在途 chat 流，纳入同一取消注册表
        control = register_chat_stream(session_id)
        try:
            # M2b：路由命中网关**且内核声明 resume 能力**时优先走网关续跑；
            # 未声明 resume 的内核（如 DSH）run_gateway_resume 直接返回 False
            # → 落到下方 butler 续跑（无挂起 checkpoint 时如实回 no_pending_task）。
            # 判据单一真源 = 内核能力声明，见 chat_paths/gateway_path.run_gateway_resume。
            if _use_gateway_route(context, params, str(params.get("message") or "")):
                handled = await run_gateway_resume(context, params, send_chunk)
                if handled:
                    return
                # 网关未产出 → 继续落到默认续跑路径
            from backend.handlers.chat_paths.pydantic_agent_path import run_pydantic_agent_resume

            async for chunk in run_pydantic_agent_resume(context, params, send_chunk, control=control):
                if control.cancelled:
                    break
                yield chunk
            if control.cancelled:
                await emit_cancelled_notice(send_chunk, control)
        except Exception as exc:
            logger.exception("chat.resume failed")
            await emit_graded_error(send_chunk, exc, control=control, extra_technical="chat.resume")
        finally:
            unregister_chat_stream(control)
            reset_trace_id(_trace_token)

    registry.register("chat.resume", chat_resume)

    # ── chat.compress_context ──
    async def chat_compress_context(
        messages: list[dict[str, str]] | None = None,
        max_tokens: int = 4000,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """压缩对话上下文 — 减少长对话的 token 消耗

        Args:
            messages: 消息列表 [{role, content}, ...]
            max_tokens: token 阈值，超过此值触发压缩

        Returns:
            {"messages": [...], "was_compressed": bool, "original_count": int, "compressed_count": int}
        """
        if not messages:
            return {"messages": [], "was_compressed": False, "original_count": 0, "compressed_count": 0}
        try:
            from services.context_compressor import compress_context

            result = compress_context(messages, max_tokens=max_tokens)
            return {
                "messages": result.messages,
                "was_compressed": result.was_compressed,
                "original_count": result.original_count,
                "compressed_count": result.compressed_count,
                "summary": result.summary,
            }
        except Exception:
            logger.exception("context compression failed")
            return {"messages": messages, "was_compressed": False, "original_count": len(messages), "compressed_count": len(messages)}

    registry.register("chat.compress_context", chat_compress_context)

    # ── chat.history ──
    async def chat_history(session_id: str = "", limit: int = 50, **kwargs: Any) -> list[dict[str, Any]]:
        repo = getattr(context, "chat_session_repository", None)
        if not repo:
            return []
        # 帧守卫（2026-10-15）：limit<=0 从"全量"收紧为默认 50，上限 clamp 2000
        limit = clamp_limit(limit, default=50)
        if session_id:
            sid, _client = _resolve_session_id(repo, session_id)
            if sid is None:
                return []
            messages = await asyncio.to_thread(repo.get_messages, sid)
            return messages[-limit:]
        sessions = await asyncio.to_thread(repo.list_sessions)
        return sessions[:limit]

    registry.register("chat.history", chat_history)

    # ── chat.save_messages ──
    # 限制：单次 save 最多 5000 条消息，单条 content 最长 200KB。
    # 防止恶意客户端一次性塞满磁盘 / 触发文件锁超时（参见审查报告 [HIGH] H4）。
    _CHAT_MAX_MESSAGES_PER_SAVE = 5000
    _CHAT_MAX_CONTENT_BYTES = 200 * 1024
    _CHAT_MAX_TOTAL_BYTES = 5 * 1024 * 1024  # 5MB total per call

    async def chat_save_messages(
        session_id: str = "",
        title: str = "",
        messages: list | None = None,
        **kwargs: Any,
    ) -> bool:
        """保存会话消息到 chat_session_repository（与 chat.history 同后端）。

        历史 bug: 之前写到独立的 chat_conversations.json，但 chat.history 走
        chat_session_repository（SQLite/JSON 分片），两者永远查不到对方的数据。
        本次统一为同一仓储；旧 chat_conversations.json 由 _migrate_legacy_chat_conversations
        在首次访问 list_conversations / get_conversation 时自动迁移并删除。
        """
        if not session_id:
            return False
        repo = getattr(context, "chat_session_repository", None)
        if not repo:
            return False

        msgs = messages or []
        if len(msgs) > _CHAT_MAX_MESSAGES_PER_SAVE:
            logger.warning("chat.save_messages 拒绝：消息数 %d 超过上限 %d", len(msgs), _CHAT_MAX_MESSAGES_PER_SAVE)
            return False
        # Total byte cap check
        total_bytes = sum(len((m.get("message") or m.get("content") or "").encode("utf-8", errors="ignore")) for m in msgs)
        if total_bytes > _CHAT_MAX_TOTAL_BYTES:
            logger.warning("chat.save_messages 拒绝：总字节数 %d 超过上限 %d", total_bytes, _CHAT_MAX_TOTAL_BYTES)
            return False
        for m in msgs:
            content = m.get("message") or m.get("content") or ""
            if isinstance(content, str) and len(content.encode("utf-8", errors="ignore")) > _CHAT_MAX_CONTENT_BYTES:
                logger.warning("chat.save_messages 拒绝：单条消息超过 %d 字节", _CHAT_MAX_CONTENT_BYTES)
                return False

        sid, client_id = _resolve_session_id(repo, session_id)
        if sid is None:
            return False

        def _do_save() -> bool:
            # 确保 session 在索引中存在（幂等）。注意不能只靠 get_session：
            # meta.json 存在但索引条目丢失的孤立会话，get_session 仍返回非空，
            # 导致索引永远不被修复、list_sessions 看不见该会话（对话记录"丢失"）。
            existing = repo.get_session(sid) if hasattr(repo, "get_session") else None
            if existing is None:
                # 新建一个会话，但要保留前端给定的 sid（不让仓储自动分配 id）
                _ensure_session_with_id(repo, sid, title or "新对话", client_id=client_id)
            else:
                _ensure_session_with_id(repo, sid, title or existing.get("title") or "新对话", client_id=client_id)
                if title and title != existing.get("title"):
                    repo.update_session(sid, {"title": title})
            # 用 sync_messages 替换全部消息（前端总是发完整列表）
            repo.sync_messages(sid, msgs)
            # sync_messages 会重写 meta.json，丢失 client_id 等扩展字段 —
            # 这里从 sessions_index 取回完整条目，回写到 meta，保证 get_session 能返回 client_id
            if client_id and hasattr(repo, "_load_sessions_index") and hasattr(repo, "_save_meta"):
                try:
                    for entry in repo._load_sessions_index() or []:
                        if entry.get("id") == sid:
                            meta = repo._load_meta(sid) if hasattr(repo, "_load_meta") else {}
                            meta.update({k: v for k, v in entry.items() if k != "id"})
                            repo._save_meta(sid, meta)
                            break
                except Exception:
                    logger.debug("post-sync meta refresh failed", exc_info=True)
            return True

        try:
            return await asyncio.to_thread(_do_save)
        except Exception:
            logger.exception("chat.save_messages failed")
            return False

    registry.register("chat.save_messages", chat_save_messages)

    # ── chat.list_conversations ──
    async def chat_list_conversations(**kwargs: Any) -> list[dict[str, Any]]:
        """列出所有已保存会话摘要（不含消息正文）。"""
        repo = getattr(context, "chat_session_repository", None)
        if not repo:
            return []
        # 触发一次性迁移
        await asyncio.to_thread(_migrate_legacy_chat_conversations, context, repo)

        sessions = await asyncio.to_thread(repo.list_sessions)
        result = []
        for s in sessions:
            # 优先暴露前端原始 client_id（UUID），保持前后端 session_id 一致
            sid_value = s.get("client_id") or s.get("id")
            sid_str = str(sid_value) if sid_value is not None else ""
            # 过滤测试残留会话（pytest 对真实数据跑出的 __test_* / test-uuid-*）
            if sid_str.startswith("__test") or sid_str.startswith("test-uuid"):
                continue
            result.append(
                {
                    "id": sid_str,
                    "title": s.get("title", ""),
                    "updated_at": s.get("updated_at", ""),
                    "message_count": s.get("message_count", 0),
                }
            )
        # 帧守卫（2026-10-15）：裸 list 契约不变，超 2000 条截断 + 告警
        return cap_list(result, method="chat.list_conversations")

    registry.register("chat.list_conversations", chat_list_conversations)

    # ── chat.get_conversation ──
    async def chat_get_conversation(session_id: str = "", **kwargs: Any) -> dict[str, Any] | None:
        """获取单个会话的完整数据（含消息）。"""
        if not session_id:
            return None
        repo = getattr(context, "chat_session_repository", None)
        if not repo:
            return None
        await asyncio.to_thread(_migrate_legacy_chat_conversations, context, repo)

        sid, _client = _resolve_session_id(repo, session_id)
        if sid is None:
            return None
        meta = await asyncio.to_thread(repo.get_session, sid)
        if meta is None:
            return None
        messages = await asyncio.to_thread(repo.get_messages, sid)
        return {
            "id": str(meta.get("client_id") or sid),
            "title": meta.get("title", ""),
            "updated_at": meta.get("updated_at", ""),
            "messages": messages,
        }

    registry.register("chat.get_conversation", chat_get_conversation)

    # ── chat.delete_conversation ──
    async def chat_delete_conversation(session_id: str = "", **kwargs: Any) -> bool:
        """删除后端保存的会话（索引条目 + 会话目录）。

        前端删除此前只在本地 store 生效并写 tombstone，后端副本永久残留；
        本方法让删除真正落到后端存储。
        """
        if not session_id:
            return False
        repo = getattr(context, "chat_session_repository", None)
        if not repo:
            return False
        sid, _client = _resolve_session_id(repo, session_id)
        if sid is None:
            return False
        try:
            await asyncio.to_thread(repo.delete_session, sid)
            return True
        except Exception:
            logger.exception("chat.delete_conversation failed for %s", session_id)
            return False

    registry.register("chat.delete_conversation", chat_delete_conversation)

    # ── memory.search ──

    async def memory_search(query: str = "", limit: int = 10, **kwargs: Any) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        if context.memory_index_service:
            results = await asyncio.to_thread(
                context.memory_index_service.recall,
                query,
                top_k=limit,
            )
            return results.get("items", [])
        return []

    registry.register_with_schema(
        "memory.search",
        memory_search,
        schema={"properties": {"limit": {"type": "integer"}, "query": {"type": "string"}}, "type": "object"},
    )

    # ── memory.recent ──
    async def memory_recent(limit: int = 20, **kwargs: Any) -> list[dict[str, Any]]:
        if context.memory_index_service:
            results = await asyncio.to_thread(
                context.memory_index_service.recall,
                "",
                top_k=limit,
            )
            return results.get("items", [])
        return []

    registry.register_with_schema(
        "memory.recent",
        memory_recent,
        schema={"properties": {"limit": {"type": "integer"}}, "type": "object"},
    )

    # ── memory.related ──
    async def memory_related(
        text: str = "",
        memory_id: str = "",
        limit: int = 8,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        if memory_id and context.memory_linker and context.memory_index_service:
            recall_results = await asyncio.to_thread(
                context.memory_index_service.recall,
                "",
                top_k=50,
            )
            lookup = {}
            for item in recall_results.get("items", []):
                eid = item.get("entry_id") or item.get("id", "")
                if eid:
                    lookup[eid] = item
            results = await asyncio.to_thread(
                context.memory_linker.get_linked_memories_with_details,
                memory_id,
                lookup,
            )
            return results[:limit]
        if text and context.memory_index_service:
            results = await asyncio.to_thread(
                context.memory_index_service.recall,
                text,
                top_k=limit,
            )
            return results.get("items", [])
        return []

    registry.register_with_schema(
        "memory.related",
        memory_related,
        schema={"properties": {"limit": {"type": "integer"}, "memory_id": {"type": "string"}, "text": {"type": "string"}}, "type": "object"},
    )

    # ── memory.related_text ──
    async def memory_related_text(text: str = "", limit: int = 5, **kwargs: Any) -> list[dict[str, Any]]:
        if not text.strip() or not context.memory_index_service:
            return []
        return await asyncio.to_thread(
            context.memory_index_service.find_related,
            text,
            limit=limit,
        )

    registry.register_with_schema(
        "memory.related_text",
        memory_related_text,
        schema={"properties": {"limit": {"type": "integer"}, "text": {"type": "string"}}, "type": "object"},
    )

    # ── memory.delete（U3，S3-5，2026-09-16）──
    # 软删（revoke 语义）：标记 revoked + 审计，召回双路径与检索引擎同步剔除；
    # 条目保留在索引 JSON 中可恢复。物理硬删（含回收站/恢复 UI）留待专门批次，
    # 本 RPC 不开新的不可逆面。entry_id 由各 memory.* 返回项提供（_format_entry 归一补写）。
    async def memory_delete(
        entry_id: str = "",
        reason: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        from backend.i18n import t

        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return {"success": False, "error": t("error.missing_param", name="entry_id")}
        svc = getattr(context, "memory_index_service", None)
        if svc is None or not hasattr(svc, "revoke_memory"):
            return {"success": False, "error": t("memory.service_unavailable")}
        ok = await asyncio.to_thread(svc.revoke_memory, entry_id, reason=str(reason or ""), revoked_by="user")
        if not ok:
            return {"success": False, "error": t("memory.entry_not_found", entry_id=entry_id), "entry_id": entry_id}
        return {"success": True, "entry_id": entry_id, "mode": "revoked"}

    registry.register_with_schema(
        "memory.delete",
        memory_delete,
        schema={
            "type": "object",
            "properties": {
                "entry_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    )

    # ── memory.list / memory.restore（U3 批1，S3-5 面板案，2026-09-16）──
    # 面板专用：真分页 + updated_at 倒序 + revoked 显隐 + 恢复原语。
    # 刻意不复用 memory.recent 作面板源——其空查询兜底是 focus-topics
    # 相关性召回（审计 §10.1），语义名不副实且 min(top_k,20) 无法翻页。
    async def memory_list(
        limit: int = 50,
        offset: int = 0,
        include_revoked: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from backend.i18n import t

        svc = getattr(context, "memory_index_service", None)
        if svc is None or not hasattr(svc, "list_entries"):
            return {"success": False, "error": t("memory.service_unavailable")}
        try:
            lim = int(limit or 50)
            off = int(offset or 0)
        except (TypeError, ValueError):
            return {"success": False, "error": t("error.invalid_param", name="limit/offset")}
        result = await asyncio.to_thread(
            svc.list_entries,
            limit=lim,
            offset=off,
            include_revoked=bool(include_revoked),
        )
        return {"success": True, **(result or {})}

    async def memory_restore(
        entry_id: str = "",
        reason: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        from backend.i18n import t

        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return {"success": False, "error": t("error.missing_param", name="entry_id")}
        svc = getattr(context, "memory_index_service", None)
        if svc is None or not hasattr(svc, "restore_memory"):
            return {"success": False, "error": t("memory.service_unavailable")}
        ok = await asyncio.to_thread(svc.restore_memory, entry_id, reason=str(reason or ""), restored_by="user")
        if not ok:
            return {"success": False, "error": t("memory.entry_not_recoverable", entry_id=entry_id), "entry_id": entry_id}
        return {"success": True, "entry_id": entry_id, "mode": "restored"}

    registry.register_with_schema(
        "memory.list",
        memory_list,
        schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
                "include_revoked": {"type": "boolean"},
            },
        },
    )
    registry.register_with_schema(
        "memory.restore",
        memory_restore,
        schema={
            "type": "object",
            "properties": {
                "entry_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    )

    # ── tags.list ──
    async def tags_list(**kwargs: Any) -> list[str]:
        """Get all tags from notes and tasks."""
        tags: set[str] = set()
        if context.personal_note_repository:
            notes = await asyncio.to_thread(context.personal_note_repository.list_notes)
            for n in notes[:500]:
                for t in n.get("tags", []):
                    if isinstance(t, str) and t.strip():
                        tags.add(t.strip())
        if context.personal_task_repository:
            tasks = await asyncio.to_thread(context.personal_task_repository.list_tasks)
            for t in tasks[:500]:
                for tag in t.get("tags", []):
                    if isinstance(tag, str) and tag.strip():
                        tags.add(tag.strip())
        return sorted(tags)

    registry.register("tags.list", tags_list)

    # ── memory.save_conversation_anchor ──
    async def memory_save_conversation_anchor(
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
        text: str = "",
        **kwargs: Any,
    ) -> bool:
        """保存对话锚点 — 从对话中提取关键信息存入记忆。

        Args:
            session_id: 会话 ID
            messages: 消息列表（前端传入）
            text: 文本（后端 dispatch 使用）
        """
        if not session_id:
            return False
        content = text or ""
        if not content and messages:
            # 从消息中提取最后几条作为锚点
            recent = messages[-3:] if len(messages) >= 3 else messages
            content = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')[:200]}" for m in recent if m.get("content"))
        if not content:
            return False
        try:
            from core.events import AppEvent, event_bus
            from datetime import datetime, timezone

            event_bus.emit(
                AppEvent(
                    "memory.auto_extract",
                    payload={
                        "source": f"chat:{session_id}",
                        "text": content,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            )
            return True
        except Exception as exc:
            # N-F（2026-09-17）：debug→warning。返回 False 此前无任何可见日志，
            # 锚点保存静默失败不可排查。返回值契约不变（bool）。
            logger.warning("save_conversation_anchor failed: %s", exc, exc_info=True)
            return False

    registry.register_with_schema(
        "memory.save_conversation_anchor",
        memory_save_conversation_anchor,
        schema={
            "properties": {"messages": {"items": {"type": "object"}, "type": "array"}, "session_id": {"type": "string"}, "text": {"type": "string"}},
            "type": "object",
        },
    )

    # ── T5 修复：memory.auto_extract 生产订阅者 ──────────────────────────
    # 此前本文件只 emit "memory.auto_extract"，全库无任何生产订阅者，锚点
    # 永远不会写入 assistant_memory_index（memory/index.py index_memory 是
    # 唯一写入方），导致 memory.surface_for_input / memory.related 召回恒空。
    # 订阅在 register_chat_memory 阶段挂载（生产启动路径仅注册一次，与
    # memory/context.py、vocab_mood.py 的挂载方式一致）。
    # event_bus.emit 为同步分发；为保证「save_conversation_anchor 返回即可
    # 召回」的语义，入库在订阅者内同步完成（小体量索引写，频率为用户级）。
    def _consume_memory_auto_extract(event: Any) -> None:
        payload = getattr(event, "payload", None) or {}
        anchor_text = str(payload.get("text") or "").strip()
        if not anchor_text:
            return
        index_service = getattr(context, "memory_index_service", None)
        if index_service is None:
            return
        try:
            index_service.index_memory(
                {
                    "content": anchor_text,
                    "type": "factual",
                    "confidence": 0.8,
                    "source": str(payload.get("source") or "chat"),
                    "tags": ["conversation_anchor"],
                    "reason": "memory.auto_extract conversation anchor",
                }
            )
        except Exception:
            logger.debug("memory.auto_extract consume failed", exc_info=True)

    try:
        from core.events import event_bus

        event_bus.on("memory.auto_extract", _consume_memory_auto_extract)
    except Exception:
        logger.debug("memory.auto_extract subscriber install failed", exc_info=True)

    # ── memory.surface_for_input ──
    async def memory_surface_for_input(
        text: str = "",
        limit: int = 5,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """根据用户输入浮现相关记忆（主动上下文提示）

        与 memory.related_text 类似，但专为输入框联想场景优化：
        - 更短的超时
        - 返回轻量结果（仅 id / summary / score）
        """
        if not text.strip() or not context.memory_index_service:
            return []
        try:
            results = await asyncio.to_thread(
                context.memory_index_service.find_related,
                text,
                limit=limit,
            )
            if isinstance(results, dict):
                items = results.get("items", [])
            elif isinstance(results, list):
                items = results
            else:
                items = []
            surfaced = []
            for item in items[:limit]:
                entry_id = item.get("entry_id") or item.get("id", "")
                # recall/_format_entry 返回的条目只有 snippet（无 summary/content
                # 字段），需逐级回退，否则索引条目会被下一步过滤掉（T5 沉淀链）。
                summary = str(item.get("summary") or item.get("content") or item.get("snippet") or item.get("title") or "")
                if entry_id and summary:
                    surfaced.append(
                        {
                            "id": entry_id,
                            "summary": summary[:150],
                            "score": item.get("score", 0.0),
                            "timestamp": item.get("timestamp", 0),
                            "source_type": item.get("source_type") or item.get("type", ""),
                            "title": item.get("title", ""),
                        }
                    )
            return surfaced
        except Exception:
            logger.debug("memory.surface_for_input failed", exc_info=True)
            return []

    registry.register_with_schema(
        "memory.surface_for_input",
        memory_surface_for_input,
        schema={"properties": {"limit": {"type": "integer"}, "text": {"type": "string"}}, "type": "object"},
    )
