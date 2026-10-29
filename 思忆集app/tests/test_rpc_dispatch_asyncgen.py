"""dispatch() 对 async generator handler 的回归测试（P2 契约修复）。

根因：dispatch() 曾对任何 handler 执行 asyncio.wait_for(handler(**params))，
而 chat.send / chat.resume 这类 async generator 函数返回的 async_generator
不是 awaitable，导致 TypeError: An asyncio.Future, a coroutine or an
awaitable is required → 客户端收到 -32603 Internal error。
（正常前端带 stream:true 走 dispatch_stream 不受影响；任何不带 stream:true
的调用，含空参普查，都会炸。）

修复：dispatch() 检测 handler(**params) 结果为 async generator 时，迭代
收集全部 chunk 以 list 返回（超时语义与 dispatch_stream 一致：相邻 chunk
之间的空闲超时）；普通 awaitable handler 行为不变。
"""

import asyncio

import pytest

from backend.method_registry import MethodRegistry
from core.errors import RPCTimeoutError


async def test_dispatch_collects_asyncgen_chunks():
    """async gen handler 经 dispatch（无 stream 标志）应收集全部 chunk 而非 TypeError。"""
    registry = MethodRegistry()

    async def gen_handler(**params):
        for i in range(3):
            yield f"chunk-{i}"

    registry.register("demo.stream", gen_handler)
    result = await registry.dispatch("demo.stream", {})
    assert result == ["chunk-0", "chunk-1", "chunk-2"]


async def test_dispatch_asyncgen_empty_yields_empty_list():
    """空参调用（census 场景）：立即返回的 async gen 应得到 [] 而非 -32603。"""
    registry = MethodRegistry()

    async def gen_handler(**params):
        return
        yield  # pragma: no cover - 使其成为 async generator

    registry.register("demo.empty", gen_handler)
    assert await registry.dispatch("demo.empty", {}) == []


async def test_dispatch_awaitable_handler_unchanged():
    """普通 awaitable handler 行为不变：返回值原样透传。"""
    registry = MethodRegistry()

    async def plain_handler(**params):
        return {"ok": True, "echo": params.get("x")}

    registry.register("demo.plain", plain_handler)
    assert await registry.dispatch("demo.plain", {"x": 1}) == {"ok": True, "echo": 1}


async def test_dispatch_asyncgen_idle_timeout():
    """超时语义与 dispatch_stream 一致：相邻 chunk 空闲超时 → RPCTimeoutError。"""
    registry = MethodRegistry()

    async def slow_gen(**params):
        yield "first"
        await asyncio.sleep(5)
        yield "never"  # pragma: no cover

    registry.register("demo.slow", slow_gen)
    with pytest.raises(RPCTimeoutError):
        await registry.dispatch("demo.slow", {}, timeout=0.05)


async def test_dispatch_asyncgen_handler_exception_propagates():
    """async gen 内部抛出的业务异常应原样向上传播。"""
    registry = MethodRegistry()

    async def bad_gen(**params):
        yield "ok"
        raise RuntimeError("boom")

    registry.register("demo.bad", bad_gen)
    with pytest.raises(RuntimeError, match="boom"):
        await registry.dispatch("demo.bad", {})


async def test_chat_send_non_stream_degradation():
    """模拟 chat.send 最小用例：async gen + 可选 send_chunk 参数。

    - dispatch（不带 stream:true）路径：收集完整输出返回；
    - 空参调用不再 TypeError / -32603；
    - dispatch_stream（stream:true）语义不受影响。
    """
    registry = MethodRegistry()
    sent: list[tuple[str, str]] = []

    async def send_chunk(chunk: str, chunk_type: str = "chunk"):
        sent.append((chunk, chunk_type))

    async def chat_send(send_chunk=None, **params):
        message = params.get("message", "")
        if not message:
            # 与真实 _chat_memory.chat_send 一致：空消息时仅在流式通道发 error chunk
            if send_chunk is not None:
                await send_chunk("消息不能为空", chunk_type="error")
            return
        for word in message.split():
            yield word

    registry.register("chat.send", chat_send)

    # 1) 不带 stream:true：逐块收集，返回完整输出
    result = await registry.dispatch("chat.send", {"message": "你好 世界"})
    assert result == ["你好", "世界"]
    assert sent == []  # dispatch 路径不注入 send_chunk

    # 2) 空参普查（曾触发 TypeError → -32603）
    assert await registry.dispatch("chat.send", {}) == []
    assert await registry.dispatch("chat.send", None) == []

    # 3) stream:true 走 dispatch_stream：行为保持不变
    chunks = [c async for c in registry.dispatch_stream("chat.send", {"message": "a b"}, send_chunk)]
    assert chunks == ["a", "b"]
    assert sent == []  # 空 message 分支未触发；正常 chunk 经迭代器返回

    # 4) dispatch_stream 空消息：send_chunk 注入，收到 error chunk、无普通 chunk
    err_chunks = [c async for c in registry.dispatch_stream("chat.send", {}, send_chunk)]
    assert err_chunks == []
    assert ("消息不能为空", "error") in sent
