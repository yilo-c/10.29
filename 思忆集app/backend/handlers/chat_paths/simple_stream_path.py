"""Simple streaming path — final fallback, handles all chat responses.

Runs the AI client's sync generator in a background thread, bridges chunks
to the async event loop via asyncio.Queue + call_soon_threadsafe.

QUA-13 / V1-3 两项修复：
- 真取消：后台线程在每个 chunk 边界与「队列满」重试间隙检查 ``control.cancelled``，
  命中即 ``gen.close()`` —— 关闭 provider 生成器才会断开底层 HTTP 流、停止继续产
  token；已产出的部分由 ``ai.client.base.chat_completion_stream`` 的用量包装器在
  GeneratorExit 路径上如实记账（success=True + 实际累计文本）。
  旧实现这里只 ``future.result(timeout=30)`` 死等，消费端一旦放弃，线程会带着
  provider 流继续跑满 30 秒 —— 这正是「停止按钮假取消、后端继续烧 token」的根因。
- 错误分型：不再一律 "生成失败，请重试"，改走 ``emit_graded_error``（复用
  ``ai.llm_resilience.classify_error`` 既有分级）。type=error chunk 仍是用户友好
  纯文本（保持既有契约），另发一条 type=notice chunk 携带 error_kind + 技术细节。
"""
from __future__ import annotations
import asyncio
import logging
import threading
import time
from typing import Any, AsyncIterator

from backend.handlers.chat_paths.helpers import (
    ChatStreamControl,
    emit_graded_error,
)

logger = logging.getLogger("siyi.chat_paths.simple_stream")

_PUSH_ACK_TIMEOUT = 5.0
_DELIVER_RETRIES = 40
_DELIVER_BACKOFF = 0.05
# sentinel 重试若干轮仍挤不进队列（消费端已弃读）→ 开始腾位，保证终止标记必达
_SENTINEL_ROOM_AFTER_RETRY = 20


async def run_simple_stream_path(
    context, msgs, send_chunk,
    preferred_provider=None, preferred_model=None,
    control: ChatStreamControl | None = None,
) -> AsyncIterator[str]:
    """Final fallback: pure streaming chat completion."""
    ai_client = getattr(context, "ai_client", None)
    if ai_client is None:
        await emit_graded_error(
            send_chunk,
            RuntimeError("AI client not available — no provider configured"),
            control=control,
        )
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    sentinel = object()
    error_container: list[Exception] = []

    def _cancelled() -> bool:
        return control is not None and control.cancelled

    def _deliver(payload: Any, *, honor_cancel: bool, make_room: bool = False) -> bool:
        """线程侧把一项投递进事件循环队列；队列满时轮询重试。

        返回 False 表示应放弃（已取消 / 事件循环已关闭 / 重试耗尽）。用
        ``put_nowait`` + call_soon_threadsafe 而非 ``run_coroutine_threadsafe(
        queue.put(...))``：后者的挂起协程无法从线程侧取消，消费端一旦放弃就会
        白等满超时（旧实现的 30s 死等即由此而来）。

        ``make_room=True``（仅 sentinel 用）：队列被弃读的残余占满时丢弃最旧一项，
        保证终止标记必定送达——否则消费端会永远阻塞在 ``queue.get()`` 上挂死。
        """
        for attempt in range(_DELIVER_RETRIES):
            ack = threading.Event()
            outcome: list[bool] = [False]
            room = make_room and attempt >= _SENTINEL_ROOM_AFTER_RETRY

            def _put_nowait() -> None:
                try:
                    queue.put_nowait(payload)
                    outcome[0] = True
                except asyncio.QueueFull:
                    if room:
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        try:
                            queue.put_nowait(payload)
                            outcome[0] = True
                        except asyncio.QueueFull:
                            outcome[0] = False
                    else:
                        outcome[0] = False
                finally:
                    ack.set()

            try:
                loop.call_soon_threadsafe(_put_nowait)
            except RuntimeError:
                return False  # 循环已关闭（客户端断开）
            ack.wait(timeout=_PUSH_ACK_TIMEOUT)
            if outcome[0]:
                return True
            if honor_cancel and _cancelled():
                return False
            time.sleep(_DELIVER_BACKOFF)
        logger.debug("simple_stream delivery retries exhausted (queue full)")
        return False

    def _collect() -> None:
        """Run in background thread — iterate sync generator, push to async queue."""
        gen: Any = None
        try:
            gen = ai_client.chat_completion_stream(
                messages=msgs, temperature=0.2, max_tokens=2000,
            )
            for chunk in gen:
                if _cancelled():
                    break
                if isinstance(chunk, str):
                    text = chunk
                elif isinstance(chunk, dict):
                    text = chunk.get("text") or chunk.get("content") or ""
                else:
                    text = str(chunk)
                if text:
                    if not _deliver(text, honor_cancel=True):
                        break
                if _cancelled():
                    break
        except Exception as exc:
            error_container.append(exc)
        finally:
            # ★ 取消传播的核心一步：显式关闭 provider 生成器。GeneratorExit 注入后
            # 底层 requests/httpx 流随之关闭，远端不再继续产 token。
            if gen is not None:
                try:
                    gen.close()
                except Exception:  # noqa: BLE001 — 已耗尽/已关闭时 close 可能抛
                    logger.debug("provider stream close failed", exc_info=True)
            _deliver(sentinel, honor_cancel=False)

    thread = threading.Thread(target=_collect, daemon=True)
    thread.start()

    cancelled = False
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if _cancelled():
                cancelled = True
                # 继续排空（不再下发）避免生产线程阻塞在队列满重试上迟迟不退出
                await _drain_until_sentinel(queue, sentinel, thread)
                break
            if control is not None:
                control.note_delta(item)
            if send_chunk:
                await send_chunk(item, chunk_type="chunk")
            yield item
    except GeneratorExit:
        # 上层（chat_send / ws 断连）放弃本流：置位标志让线程立即收尾
        if control is not None:
            control.request_cancel("consumer_gone")
        raise
    except asyncio.CancelledError:
        # 承载任务被取消（chat.cancel 硬兜底 / 断连）：同样要让线程立刻断开上游流
        if control is not None:
            control.request_cancel("task_cancelled")
        raise
    except Exception as exc:
        logger.exception("simple_stream path failed")
        payload = await emit_graded_error(send_chunk, exc, control=control)
        yield payload["friendly"]
    else:
        if cancelled:
            # 终态 cancelled 的 notice 由 dispatcher（chat_send）统一下发，避免重复；
            # 这里只负责停止继续产 delta 并正常收尾。
            logger.info(
                "simple_stream cancelled by chat.cancel: %s",
                control.summary() if control else {},
            )
            if send_chunk:
                await send_chunk("", chunk_type="done")
        elif error_container:
            # 后台线程异常（流已部分产出）：补发分型错误，
            # 不重抛——重抛会打断上层并吞掉已产出的部分内容。
            logger.warning("simple_stream background error: %s", error_container[0])
            payload = await emit_graded_error(
                send_chunk, error_container[0], control=control,
                extra_technical="background_thread",
            )
            yield payload["friendly"]
        elif send_chunk:
            await send_chunk("", chunk_type="done")
    finally:
        thread.join(timeout=5)


async def _drain_until_sentinel(queue: asyncio.Queue, sentinel: object, thread: threading.Thread) -> None:
    """取消后把队列残余 chunk 消费掉，直到生产线程投递 sentinel（或线程已退出）。"""
    for _ in range(1000):
        if not thread.is_alive() and queue.empty():
            return
        try:
            item = await asyncio.wait_for(queue.get(), timeout=0.5)
        except (asyncio.TimeoutError, TimeoutError):
            return
        if item is sentinel:
            return
