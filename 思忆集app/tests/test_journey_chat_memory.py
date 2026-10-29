"""T5-J4 · L2 旅程 E2E：对话 → 历史 → 记忆锚点沉淀链。

旅程（生产前端契约；AI 一律 mock，无真实 AI 调用 [pending-live-AI]）：
  chat.send（mock AI 固定回复，simple_stream 保底路径）
  → chat.save_messages（前端持久化契约）→ chat.history 可见
  → memory.save_conversation_anchor → memory.surface_for_input /
  memory.related 应可召回锚点（沉淀链完整）。

T5 修复记录（2026-08-30）：
  backend/handlers/_chat_memory.py 的 register_chat_memory 现挂载
  "memory.auto_extract" 生产订阅者，把锚点文本经 memory/index.py index_memory
  写入 assistant_memory_index，surface_for_input / related 可召回 → 沉淀链
  完整（原缺陷对应的两个 strict xfail 已摘除标记转正）。

Mock 边界：
  - chat.send 的 pydantic 管家路径以 monkeypatch 短路（get_butler_agent →
    None），强制走 simple_stream 保底路径，由 mock chat_completion_stream
    返回固定回复；真实 AI 链路（butler/gateway）问题归 T1。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from _fake_settings import FakeSettingsRepository
from core.settings_keys import KEY_LLM_API_KEY

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore  # noqa: E402
from memory.index import AssistantMemoryIndexService  # noqa: E402
from repositories._chat_session import LocalChatSessionRepository  # noqa: E402

SESSION_ID = "9001"
USER_TEXT = "J4旅程测试：帮我记住思忆集项目使用 Python 3.11"
AI_TEXT = "J4固定回复：已了解思忆集项目使用 Python 3.11"
ANCHOR_TEXT = "J4ANCHOR 用户偏好：思忆集项目固定使用 Python 3.11 与 pytest"


class _FakeSettingsRepo(FakeSettingsRepository):
    """2026-09-18：改为继承共享假仓储（tests/_fake_settings.py）。

    原实现 `self._data.get(key, default)` 对**未在 _data 里的 key**一律返回
    default，等于跳过了 `SETTINGS_DEFAULTS`，于是 chat_paths/helpers.py 的预检
    读 `llm_api_key` 得到空 → 判定"未配置" → 本文件 j4 用例红（报错文案与
    测试意图无关）。继承后默认值回落真源，行为与真实仓储一致。
    """

    def __init__(self, data=None):
        super().__init__(data)


class _FakeContext:
    """最小 DI 上下文：未知属性一律 None（模拟未注入的服务槽位）。"""

    def __init__(self, chat_repo, memory_index, settings):
        self.chat_session_repository = chat_repo
        self.memory_index_service = memory_index
        self.settings_repository = settings
        self.memory_linker = None
        self.personal_note_repository = None
        self.personal_task_repository = None

    def __getattr__(self, name):
        return None


class _MockStreamAI:
    """mock AI：chat_completion_stream 同步生成器（simple_stream 契约）。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[dict] = []

    def chat_completion_stream(self, messages, temperature=0.2, max_tokens=2000, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "temperature": temperature})
        yield self.reply


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _build(tmp_path):
    """tmp_path 真实仓储 + mock 上下文 + chat/memory 域方法注册。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    chat_repo = LocalChatSessionRepository(store)
    memory_index = AssistantMemoryIndexService(data_store=store)
    # 显式声明 llm_api_key 非空：本用例验证的是「管家路径的流式回传」，
    # 而非 chat_paths/helpers.py 的"未配置"预检分支（后者另有专门用例覆盖）。
    settings = _FakeSettingsRepo(
        {"chat_route": "legacy", KEY_LLM_API_KEY: "test-llm-key-0000"}
    )  # 显式钉死管家路径（缺省已回翻 legacy，此处防御性显式）
    ctx = _FakeContext(chat_repo, memory_index, settings)

    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    return ctx, registry


@pytest.fixture()
def _force_simple_stream(monkeypatch):
    """短路 pydantic 管家路径（AI 执行引擎 mock 边界，见模块 docstring）。"""
    monkeypatch.setattr("assistant.butler_agent.get_butler_agent", lambda ctx: None, raising=False)
    monkeypatch.setattr("assistant.agents.butler_agent.get_butler_agent", lambda ctx: None, raising=False)


def test_j4_chat_send_streams_mock_reply(tmp_path, _force_simple_stream):
    """chat.send：mock AI 固定回复应经流式 chunk 返回并以 done 结束。"""
    ctx, registry = _build(tmp_path)
    ai = _MockStreamAI(AI_TEXT)
    ctx.ai_client = ai
    chunks: list[tuple[str, str]] = []

    async def send_chunk(text, chunk_type="chunk"):
        chunks.append((text, chunk_type))

    async def journey():
        got = []
        async for piece in registry.dispatch_stream("chat.send", {"message": USER_TEXT, "session_id": SESSION_ID}, send_chunk):
            got.append(piece)
        return got

    got = _run(journey())
    streamed = "".join(str(p) for p in got) + "".join(t for t, _ in chunks)
    assert AI_TEXT in streamed, f"mock AI 固定回复应流式返回: yielded={got!r} chunks={chunks!r}"
    assert any(t == "" and ct == "done" for t, ct in chunks), f"流结束应有 done chunk: {chunks!r}"
    assert ai.calls, "mock AI chat_completion_stream 应被调用"


def test_j4_chat_history_sees_conversation(tmp_path, _force_simple_stream):
    """chat.send → chat.save_messages（前端契约）→ chat.history 双向可见。"""
    ctx, registry = _build(tmp_path)
    ai = _MockStreamAI(AI_TEXT)
    ctx.ai_client = ai

    async def send_chunk(text, chunk_type="chunk"):
        return None

    async def journey():
        async for _ in registry.dispatch_stream("chat.send", {"message": USER_TEXT, "session_id": SESSION_ID}, send_chunk):
            pass
        saved = await registry.dispatch(
            "chat.save_messages",
            {
                "session_id": SESSION_ID,
                "messages": [
                    {"role": "user", "content": USER_TEXT},
                    {"role": "assistant", "content": AI_TEXT},
                ],
            },
        )
        assert saved is True, "chat.save_messages 应成功（前端持久化契约）"
        return await registry.dispatch("chat.history", {"session_id": SESSION_ID})

    history = _run(journey())
    assert isinstance(history, list) and len(history) >= 2, f"历史应含双方消息: {history!r}"
    blob = json.dumps(history, ensure_ascii=False)
    assert USER_TEXT in blob, f"用户消息应可见: {blob[:300]}"
    assert AI_TEXT in blob, f"AI 回复应可见: {blob[:300]}"


def test_j4_anchor_save_publishes_auto_extract_event(tmp_path):
    """memory.save_conversation_anchor 应发布 memory.auto_extract 事件（链路第一跳）。"""
    ctx, registry = _build(tmp_path)

    from core.events import event_bus

    seen: list = []

    def _capture(event):
        seen.append(event)

    event_bus.on("memory.auto_extract", _capture)
    try:
        ok = _run(
            registry.dispatch(
                "memory.save_conversation_anchor",
                {
                    "session_id": SESSION_ID,
                    "messages": [{"role": "user", "content": ANCHOR_TEXT}],
                },
            )
        )
        assert ok is True, "锚点保存应返回 True"
        assert any(e.event_type == "memory.auto_extract" and ANCHOR_TEXT in str(e.payload.get("text", "")) for e in seen), (
            f"应捕获含锚点文本的 auto_extract 事件: {[e.event_type for e in seen]}"
        )
    finally:
        event_bus.off("memory.auto_extract", _capture)


def test_j4_anchor_recallable_via_surface_for_input(tmp_path):
    """沉淀链最后一跳（T5 已修复，2026-08-30 转正）：

    锚点保存后 memory.surface_for_input 应召回锚点：生产订阅者
    （backend/handlers/_chat_memory.py register_chat_memory 挂载）消费
    memory.auto_extract 事件，经 memory/index.py index_memory 写入
    assistant_memory_index。
    """
    ctx, registry = _build(tmp_path)
    saved = _run(
        registry.dispatch(
            "memory.save_conversation_anchor",
            {
                "session_id": SESSION_ID,
                "messages": [{"role": "user", "content": ANCHOR_TEXT}],
            },
        )
    )
    assert saved is True
    surfaced = _run(registry.dispatch("memory.surface_for_input", {"text": "J4ANCHOR"}))
    assert surfaced, (
        "沉淀链断点：surface_for_input 应召回刚保存的锚点。根因："
        "memory.auto_extract 事件无生产订阅者（_chat_memory.py:851 仅 emit），"
        "锚点未写入 assistant_memory_index"
    )
    assert any("J4ANCHOR" in json.dumps(item, ensure_ascii=False) for item in surfaced), f"召回结果应包含锚点标记: {surfaced!r}"


def test_j4_anchor_recallable_via_memory_related(tmp_path):
    """沉淀链最后一跳（T5 已修复，2026-08-30 转正）：memory.related(text=) 同样应召回锚点。"""
    ctx, registry = _build(tmp_path)
    saved = _run(
        registry.dispatch(
            "memory.save_conversation_anchor",
            {
                "session_id": SESSION_ID,
                "messages": [{"role": "user", "content": ANCHOR_TEXT}],
            },
        )
    )
    assert saved is True
    related = _run(registry.dispatch("memory.related", {"text": "J4ANCHOR"}))
    assert related, (
        "沉淀链断点：memory.related 应召回刚保存的锚点。根因："
        "memory.auto_extract 事件无生产订阅者（_chat_memory.py:851 仅 emit），"
        "锚点未写入 assistant_memory_index"
    )
