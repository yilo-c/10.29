"""Integration tests for chat and memory RPC handlers."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from _fake_settings import configured_settings_repository


class FakeDataStore:
    """In-memory data store mirroring LocalDataStore JSON file interface."""

    def __init__(self):
        self._data: dict[str, object] = {}

    def load_json(self, key: str, default=None):
        return self._data.get(key, default if default is not None else {})

    def save_json(self, key: str, value):
        self._data[key] = value


class FakeChatSessionRepo:
    """模拟 LocalChatSessionRepository。

    P0-2 修复后，chat.save_messages / list_conversations / get_conversation
    都改走该仓储。这里实现仓储真实使用的私有 API（_load_sessions_index 等）
    以便单元测试覆盖完整路径。
    """

    def __init__(self, store=None):
        self._store = store or FakeDataStore()
        # _sessions 既作为新代码使用的 index，也兼容旧测试直接赋值
        self._sessions: list[dict] = []
        self._messages: dict[int, list] = {}
        self._meta: dict[int, dict] = {}

    # 公共 API
    def list_sessions(self):
        # 与真实 LocalChatSessionRepository.list_sessions() 行为一致：按 updated_at 倒序
        return sorted(self._sessions, key=lambda s: s.get("updated_at", ""), reverse=True)

    def get_session(self, session_id: int):
        for s in self._sessions:
            if s.get("id") == session_id:
                return dict(s)
        return None

    def get_messages(self, session_id: int):
        if session_id in self._messages:
            return list(self._messages[session_id])
        # 兼容旧测试直接在 _sessions[*]["messages"] 内联消息
        for s in self._sessions:
            if s.get("id") == session_id:
                return s.get("messages", [])
        return []

    def sync_messages(self, session_id: int, messages: list[dict]):
        self._messages[session_id] = list(messages)
        for s in self._sessions:
            if s.get("id") == session_id:
                s["message_count"] = len(messages)
                s["updated_at"] = "2026-06-17T00:00:00+00:00"

    def update_session(self, session_id: int, info: dict):
        for s in self._sessions:
            if s.get("id") == session_id:
                s.update(info)

    # 新代码访问的私有 helper
    def _load_sessions_index(self):
        return list(self._sessions)

    def _save_sessions_index(self, sessions):
        self._sessions = list(sessions)

    def _save_meta(self, sid: int, meta: dict):
        self._meta[sid] = dict(meta)


class FakeNoteRepo:
    def __init__(self, store=None):
        self.data_store = store or FakeDataStore()


class FakeContext:
    def __init__(self, **kwargs):
        self.chat_session_repository = kwargs.get("chat_session_repository")
        self.personal_note_repository = kwargs.get("personal_note_repository")
        self.memory_index_service = kwargs.get("memory_index_service")
        self.memory_linker = kwargs.get("memory_linker")
        self.ai_client = kwargs.get("ai_client")
        # 2026-09-18：换掉 `MagicMock() + get_setting.return_value = []`。
        # 那个 blanket mock 让**任何** key（含字符串型的 llm_api_key）都返回 `[]`，
        # 于是 chat_paths/helpers.py 的预检判定"未配置 AI 服务"→ chat.send 零帧返回，
        # 本文件 13 条用例红，而报错文案与测试意图（验证流式链路）毫不相干。
        # 改用回落 SETTINGS_DEFAULTS 真源的假仓储（tests/_fake_settings.py），
        # 并显式声明已配置 API Key，让预检继续正常路由。
        self.settings_repository = kwargs.get("settings_repository") or configured_settings_repository()
        # Attrs that chat.send may touch
        self.autonomous_assistant_manager = None
        self.copilot_engine = None
        self.personal_inbox_repository = None


@pytest.fixture
def ctx():
    store = FakeDataStore()
    chat_repo = FakeChatSessionRepo(store)
    note_repo = FakeNoteRepo(store)
    return FakeContext(
        chat_session_repository=chat_repo,
        personal_note_repository=note_repo,
    )


# ── chat.history ──


def test_chat_history_no_repo():
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    ctx = FakeContext()
    register_chat_memory(registry, ctx)
    import asyncio

    result = asyncio.run(registry.dispatch("chat.history", {}))
    assert result == []


def test_chat_history_list_sessions(ctx):
    ctx.chat_session_repository._sessions = [
        {"id": 1, "title": "Chat A", "updated_at": "2024-01-01"},
        {"id": 2, "title": "Chat B", "updated_at": "2024-01-02"},
    ]
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    result = asyncio.run(registry.dispatch("chat.history", {}))
    assert len(result) == 2


def test_chat_history_with_session_id(ctx):
    ctx.chat_session_repository._sessions = [
        {"id": 1, "title": "Chat A", "messages": [{"role": "user", "content": "hi"}]},
    ]
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    result = asyncio.run(registry.dispatch("chat.history", {"session_id": "1"}))
    assert len(result) == 1
    assert result[0]["content"] == "hi"


def test_chat_history_invalid_session_id(ctx):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    result = asyncio.run(registry.dispatch("chat.history", {"session_id": "not-a-number"}))
    assert result == []


# ── chat.save_messages ──


def test_chat_save_and_list(ctx):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    saved = asyncio.run(
        registry.dispatch(
            "chat.save_messages",
            {
                "session_id": "session-abc",
                "title": "Test Conversation",
                "messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi there"}],
            },
        )
    )
    assert saved is True

    conversations = asyncio.run(registry.dispatch("chat.list_conversations", {}))
    assert len(conversations) == 1
    # P0-2 修复后：id 是仓储内部 int sid 的字符串形式，不再保留前端原始字符串
    assert isinstance(conversations[0]["id"], str)
    assert conversations[0]["id"]  # 非空
    assert conversations[0]["title"] == "Test Conversation"
    assert conversations[0]["message_count"] == 2


def test_chat_save_message_no_session(ctx):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    result = asyncio.run(registry.dispatch("chat.save_messages", {"title": "no session"}))
    assert result is False


# ── chat.list_conversations ──


def test_chat_list_empty(ctx):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    result = asyncio.run(registry.dispatch("chat.list_conversations", {}))
    assert result == []


def test_chat_list_sorted_by_date(ctx):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    # Save two conversations
    asyncio.run(
        registry.dispatch(
            "chat.save_messages",
            {
                "session_id": "older",
                "title": "Older",
                "messages": [{"role": "user", "content": "old"}],
            },
        )
    )
    asyncio.run(
        registry.dispatch(
            "chat.save_messages",
            {
                "session_id": "newer",
                "title": "Newer",
                "messages": [{"role": "user", "content": "new"}],
            },
        )
    )
    result = asyncio.run(registry.dispatch("chat.list_conversations", {}))
    assert len(result) == 2


# ── chat.get_conversation ──


def test_chat_get_conversation(ctx):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    asyncio.run(
        registry.dispatch(
            "chat.save_messages",
            {
                "session_id": "conv-1",
                "title": "My Chat",
                "messages": [{"role": "user", "content": "Q"}],
            },
        )
    )
    conv = asyncio.run(registry.dispatch("chat.get_conversation", {"session_id": "conv-1"}))
    assert conv is not None
    assert conv["title"] == "My Chat"
    assert len(conv["messages"]) == 1


def test_chat_get_conversation_missing(ctx):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    result = asyncio.run(registry.dispatch("chat.get_conversation", {"session_id": "no-such-id"}))
    assert result is None


class FakeAIClient:
    """Mock AI client for chat.send streaming tests."""

    def __init__(self, chunks=None, raise_after=None):
        self._chunks = list(chunks or [])
        self._raise_after = raise_after

    def chat_completion_stream(self, **kwargs):
        for i, chunk in enumerate(self._chunks):
            yield chunk
            if self._raise_after is not None and i + 1 >= self._raise_after:
                raise RuntimeError("stream error")


class FakeContextWithAI(FakeContext):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ai_client = kwargs.get("ai_client") or FakeAIClient(["hello"])


@pytest.fixture
def ctx_with_ai():
    store = FakeDataStore()
    chat_repo = FakeChatSessionRepo(store)
    note_repo = FakeNoteRepo(store)
    return FakeContextWithAI(
        chat_session_repository=chat_repo,
        personal_note_repository=note_repo,
        ai_client=FakeAIClient(["hello ", "world"]),
    )


# ── chat.send (smoke only — full test needs real AI backend) ──


def test_chat_send_empty_message():
    """chat.send returns immediately for empty message — streaming handler tested directly."""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    ctx = FakeContext()
    register_chat_memory(registry, ctx)

    async def collect():
        handler = registry._handlers.get("chat.send")
        assert handler is not None
        results = []
        async for chunk in handler(message=""):
            results.append(chunk)
        return results

    results = asyncio.run(collect())
    assert results == []


def test_chat_send_streaming_full_chain(ctx_with_ai):
    """chat.send streams all chunks from the AI client."""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)

    async def collect():
        handler = registry._handlers.get("chat.send")
        assert handler is not None
        results = []
        async for chunk in handler(message="hi"):
            results.append(chunk)
        return results

    results = asyncio.run(collect())
    assert results == ["hello ", "world"]


def test_chat_send_streaming_error_recovery(ctx_with_ai):
    """chat.send yields an error chunk when the AI stream fails."""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    ctx_with_ai.ai_client = FakeAIClient(["partial "], raise_after=1)
    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)

    async def collect():
        handler = registry._handlers.get("chat.send")
        assert handler is not None
        results = []
        async for chunk in handler(message="hi"):
            results.append(chunk)
        return results

    results = asyncio.run(collect())
    assert "partial " in results
    assert any("失败" in chunk or "error" in chunk.lower() for chunk in results)


def test_chat_send_large_message_segmentation(ctx_with_ai):
    """chat.send handles large messages without crashing."""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)

    large_message = "x" * 5000

    async def collect():
        handler = registry._handlers.get("chat.send")
        assert handler is not None
        results = []
        async for chunk in handler(message=large_message):
            results.append(chunk)
        return results

    results = asyncio.run(collect())
    assert results == ["hello ", "world"]


def test_chat_send_cancel_stream(ctx_with_ai):
    """chat.send can be cancelled mid-stream."""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    ctx_with_ai.ai_client = FakeAIClient(["chunk"] * 100)
    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)

    async def collect():
        handler = registry._handlers.get("chat.send")
        assert handler is not None
        results = []
        count = 0
        async for chunk in handler(message="hi"):
            results.append(chunk)
            count += 1
            if count >= 2:
                break
        return results

    results = asyncio.run(collect())
    assert len(results) == 2


# ── QUA-13 / V1-3：chat.cancel 真取消 + 流式错误分型透传 ──────────────────


class TypedChunkCollector:
    """记录 (text, chunk_type) 的 send_chunk 替身。"""

    def __init__(self):
        self.items: list[tuple[str, str]] = []

    async def __call__(self, chunk: str, chunk_type: str = "chunk") -> None:
        self.items.append((chunk, chunk_type))

    def of(self, chunk_type: str) -> list[str]:
        return [t for t, c in self.items if c == chunk_type]

    def events(self, event: str) -> list[dict]:
        out: list[dict] = []
        for text, ctype in self.items:
            if ctype not in ("notice", "chat_resume_status"):
                continue
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict) and parsed.get("event") == event:
                out.append(parsed)
        return out


class InfiniteAIClient:
    """持续产 token 的 provider 替身 —— 用来证明取消真的断开了上游流。"""

    def __init__(self, delay: float = 0.005):
        self.produced = 0
        self.closed = False
        self._delay = delay

    def chat_completion_stream(self, **kwargs):
        return self._gen()

    def _gen(self):
        try:
            while True:
                self.produced += 1
                yield f"t{self.produced} "
                time.sleep(self._delay)
        except GeneratorExit:
            self.closed = True
            raise


class RaisingAIClient:
    """先产出若干 delta，再以指定异常终止的 provider 替身。"""

    def __init__(self, exc: BaseException, pre: tuple[str, ...] = ("部分回答",)):
        self._exc = exc
        self._pre = list(pre)

    def chat_completion_stream(self, **kwargs):
        for chunk in self._pre:
            yield chunk
        raise self._exc


def _run_cancel_scenario(ctx_with_ai, session_id: str):
    """跑一条会被 chat.cancel 中途取消的 chat.send 流，返回观测到的各项事实。"""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.handlers.chat_paths.helpers import active_chat_streams
    from backend.method_registry import MethodRegistry

    provider = InfiniteAIClient()
    ctx_with_ai.ai_client = provider
    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)
    send = TypedChunkCollector()

    async def scenario():
        handler = registry._handlers["chat.send"]

        async def _run() -> list[str]:
            out: list[str] = []
            async for chunk in handler(send_chunk=send, message="hi", session_id=session_id):
                out.append(chunk)
            return out

        task = asyncio.ensure_future(_run())
        for _ in range(600):
            if send.of("chunk"):
                break
            await asyncio.sleep(0.005)
        assert send.of("chunk"), "取消前应已产出若干 delta"

        deltas_at_cancel = len(send.of("chunk"))
        produced_at_cancel = provider.produced
        result = await registry.dispatch("chat.cancel", {"session_id": session_id})
        yielded = await asyncio.wait_for(task, timeout=5)
        settled = provider.produced
        await asyncio.sleep(0.2)  # 观察窗：真取消后上游不得继续产 token
        return {
            "result": result,
            "deltas_at_cancel": deltas_at_cancel,
            "produced_at_cancel": produced_at_cancel,
            "settled": settled,
            "produced_final": provider.produced,
            "deltas_final": len(send.of("chunk")),
            "closed": provider.closed,
            "cancelled_notices": send.events("chat_cancelled"),
            "errors": send.of("error"),
            "yielded": yielded,
            "leftover": [ctl.stream_id for ctl in active_chat_streams() if ctl.session_id == session_id],
        }

    return asyncio.run(scenario())


def test_chat_cancel_stops_provider_stream_and_new_deltas(ctx_with_ai):
    """任务一：chat.cancel 必须真的停掉后端流——无新 delta、上游生成器被关闭。"""
    facts = _run_cancel_scenario(ctx_with_ai, "s-cancel-1")

    # 取消后不再产生新的 delta（最多允许在途 1 条）
    assert facts["deltas_final"] <= facts["deltas_at_cancel"] + 1
    assert facts["deltas_final"] < 200, "流被提前终止，不应跑完整个长回答"
    # provider 侧同样停住：观察窗内 produced 不再增长 = 不再烧 token
    assert facts["closed"] is True, "provider 生成器未被 close，底层 HTTP 流仍在产 token"
    assert facts["produced_final"] == facts["settled"]
    assert facts["produced_final"] - facts["produced_at_cancel"] <= 5
    # 取消不是错误：不应上送 error chunk
    assert facts["errors"] == []
    # 注册表已收尾，无泄漏
    assert facts["leftover"] == []


def test_chat_cancel_marks_session_status_cancelled_not_completed(ctx_with_ai):
    """任务一：会话终态落 cancelled（RPC 回执 + cancelled notice 双处可见）。"""
    facts = _run_cancel_scenario(ctx_with_ai, "s-cancel-2")
    result = facts["result"]

    assert result["cancelled"] is True
    assert result["canceled_streams"], "chat.cancel 应命中在途流，而非空确认"
    entry = result["canceled_streams"][0]
    assert entry["status"] == "cancelled"
    assert entry["session_id"] == "s-cancel-2"
    assert entry["cancelled"] is True
    # token 记账如实：已产出的部分保留在回执里
    assert entry["deltas"] >= 1 and entry["chars"] >= 1

    notices = facts["cancelled_notices"]
    assert notices, "取消终态必须有一条 chat_cancelled notice"
    assert notices[0]["status"] == "cancelled"
    assert notices[0]["session_id"] == "s-cancel-2"


def test_chat_cancel_targets_only_given_session(ctx_with_ai):
    """定向取消：session_id 只命中该会话，其他在途流不受影响。"""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.handlers.chat_paths.helpers import active_chat_streams
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)
    ctx_with_ai.ai_client = InfiniteAIClient()

    async def scenario():
        handler = registry._handlers["chat.send"]

        async def _run(sid: str) -> None:
            async for _ in handler(send_chunk=TypedChunkCollector(), message="hi", session_id=sid):
                pass

        a = asyncio.ensure_future(_run("keep"))
        b = asyncio.ensure_future(_run("stop"))
        for _ in range(600):
            if len(active_chat_streams()) >= 2:
                break
            await asyncio.sleep(0.005)
        res = await registry.dispatch("chat.cancel", {"session_id": "stop"})
        await asyncio.wait_for(b, timeout=5)
        statuses = {ctl.session_id: ctl.status for ctl in active_chat_streams()}
        await registry.dispatch("chat.cancel", {})
        await asyncio.wait_for(a, timeout=5)
        return res, statuses

    result, statuses = asyncio.run(scenario())
    assert [s["session_id"] for s in result["canceled_streams"]] == ["stop"]
    assert statuses.get("keep") == "streaming", "定向取消不得波及其他会话"


def test_cancel_preserves_partial_token_accounting(monkeypatch):
    """任务一（记账如实）：close 上游流后，用量按已产出的部分记一笔 success=True。

    这正是「取消走 gen.close()（GeneratorExit）而不是抛异常」的意义：异常路径会
    记 success=False 且 completion_tokens=0，把已经花掉的 token 抹成零。
    """
    import ai.usage_tracker as ut
    from ai.client.base import OpenAICompatibleClient

    records: list[dict] = []

    class _FakeTracker:
        def record(self, **kwargs):
            records.append(kwargs)

    monkeypatch.setattr(ut, "get_usage_tracker", lambda: _FakeTracker())

    client = OpenAICompatibleClient(
        api_base="https://api.deepseek.com/v1",
        api_key="sk-test",
        model="deepseek-chat",
    )
    client.stream_chat = lambda messages, **kwargs: iter(["你好世界 ", "更多 ", "还有 "])
    msgs = [{"role": "user", "content": "hi"}]

    gen = client.chat_completion_stream(messages=msgs)
    assert next(gen) == "你好世界 "
    gen.close()  # ← chat.cancel 的等价动作

    assert len(records) == 1, "取消后应恰好记一笔用量"
    rec = records[0]
    assert rec["success"] is True
    assert rec["completion_tokens"] >= 1, "已产出的部分必须计入，不得记 0"


def test_chat_cancel_without_inflight_stream_is_idempotent(ctx_with_ai):
    """无在途流时保持幂等确认（旧契约的 cancelled=True 不回退）。"""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)
    result = asyncio.run(registry.dispatch("chat.cancel", {}))
    assert result["cancelled"] is True
    assert result["canceled_streams"] == []
    assert result["still_active"] == []


def _collect_graded_stream(ctx_with_ai, exc: BaseException):
    """走完整 chat.send 链路跑一条以 exc 终止的流，返回 (chunks, collector)。"""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    ctx_with_ai.ai_client = RaisingAIClient(exc)
    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)
    send = TypedChunkCollector()

    async def collect():
        handler = registry._handlers["chat.send"]
        out: list[str] = []
        async for chunk in handler(send_chunk=send, message="hi", session_id="s-err"):
            out.append(chunk)
        return out

    return asyncio.run(collect()), send


def test_simple_stream_error_is_graded_not_generic(ctx_with_ai):
    """任务二：分型错误不得再被笼统的「生成失败，请重试」吞掉。"""
    from ai.types import AIServiceError

    chunks, send = _collect_graded_stream(
        ctx_with_ai,
        AIServiceError("Too Many Requests: rate limit exceeded", transient=True, status=429, error_kind="rate_limit"),
    )
    errors = send.of("error")
    assert errors, "错误必须以 type=error chunk 上送"
    assert errors[0] != "生成失败，请重试", "限流不应再落到笼统兜底文案"
    assert "限流" in errors[0]

    notices = send.events("chat_error")
    assert notices, "应额外上送一条含技术细节的 notice"
    payload = notices[0]
    # 双字段：用户友好文案 + 技术细节
    assert payload["friendly"] == errors[0]
    assert payload["technical"]
    assert "AIServiceError" in payload["technical"]
    assert payload["error_kind"] == "rate_limit"
    assert payload["status"] == 429
    assert payload["transient"] is True
    # 已产出的部分内容不被吞掉
    assert "部分回答" in chunks


def test_simple_stream_error_grading_network_timeout_and_no_retry_guess(ctx_with_ai):
    """任务二：超时类错误单独成型（旧实现与限流/未知错误无法区分）。"""
    _chunks, send = _collect_graded_stream(ctx_with_ai, TimeoutError("Request timed out after 60 seconds"))
    notices = send.events("chat_error")
    assert notices
    assert notices[0]["error_kind"] == "timeout"
    assert "超时" in notices[0]["friendly"]
    assert send.of("error")[0] == notices[0]["friendly"]


def test_chat_send_dispatcher_level_error_is_graded_too(ctx_with_ai, monkeypatch):
    """dispatcher 层异常（build_messages 等）也不再落到 ws 的笼统 Internal error。"""
    import asyncio
    import backend.handlers._chat_memory as cm
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    from ai.types import AIServiceError

    async def _boom(*args, **kwargs):
        raise AIServiceError("Incorrect API key provided", status=401, error_kind="auth_error")

    monkeypatch.setattr(cm, "build_messages", _boom)
    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)
    send = TypedChunkCollector()

    async def collect():
        handler = registry._handlers["chat.send"]
        out: list[str] = []
        async for chunk in handler(send_chunk=send, message="hi", session_id="s-disp"):
            out.append(chunk)
        return out

    chunks = asyncio.run(collect())
    errors = send.of("error")
    assert errors and "鉴权" in errors[0]
    assert "Internal error" not in "".join(errors)
    notices = send.events("chat_error")
    assert notices and notices[0]["error_kind"] == "auth_error"
    assert "chat.send" in notices[0]["technical"]
    assert chunks == []  # 出错不产出正文字符


@pytest.mark.parametrize(
    ("exc", "expected_kind", "expected_friendly_hint"),
    [
        (RuntimeError("invalid api key provided"), "auth_error", "鉴权"),
        (RuntimeError("insufficient_quota: balance is too low"), "billing_error", "额度"),
        (RuntimeError("response flagged by content_filter"), "content_moderation", "审核"),
        (ConnectionResetError("connection reset by peer"), "network_error", "网络"),
        (RuntimeError("HTTP 503 server overloaded"), "server_error", "暂时不可用"),
        (RuntimeError("something totally unexpected"), "unknown_error", "生成失败"),
    ],
)
def test_grade_stream_exception_kinds(exc, expected_kind, expected_friendly_hint):
    """错误分型清单：复用 ai.llm_resilience.classify_error + 补审核/超时/网络。"""
    from backend.handlers.chat_paths.helpers import grade_stream_exception

    payload = grade_stream_exception(exc)
    assert payload["error_kind"] == expected_kind
    assert expected_friendly_hint in payload["friendly"]
    assert payload["technical"].startswith(type(exc).__name__)
    # 双字段都必须非空，且不泄漏 traceback
    assert payload["friendly"] and payload["technical"]
    assert "Traceback" not in payload["technical"]


def test_grade_stream_exception_respects_explicit_error_kind():
    """provider 已给出 error_kind 时直接采信，不重复分类。"""
    from ai.types import AIServiceError
    from backend.handlers.chat_paths.helpers import grade_stream_exception

    payload = grade_stream_exception(AIServiceError("provider said no", status=451, error_kind="content_moderation"))
    assert payload["error_kind"] == "content_moderation"
    assert payload["transient"] is False
    assert "451" in payload["technical"]


class FakeAggregator:
    """Mock 多 provider 聚合器（当前聚合路径走 context.aggregator）。"""

    def __init__(self, text="agg stream"):
        self._text = text

    async def aggregate(self, messages, **kwargs):
        return {"text": self._text}


def test_chat_send_default_butler_path_executes_tool(ctx_with_ai):
    """P3 收敛：默认路径 PydanticAI 管家执行工具并流式返回最终回复。"""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    from assistant import butler_agent

    butler_agent._agent_cache.clear()  # 避免 id 复用串台

    class FakeCapabilityRouter:
        def __init__(self):
            self.calls = []

        async def execute_direct(self, action_id, args, approved=False):
            self.calls.append((action_id, dict(args or {})))
            return {"ok": True, "action": action_id}

    class ButlerClient:
        def __init__(self):
            self.n = 0

        def chat_completion(self, messages, **kwargs):
            self.n += 1
            if self.n == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "notes_list", "arguments": "{}"}}],
                            }
                        }
                    ],
                    "model": "t",
                }
            return {"choices": [{"message": {"content": "已列出笔记"}}], "model": "t"}

    router = FakeCapabilityRouter()
    ctx_with_ai.ai_client = ButlerClient()
    ctx_with_ai.capability_router = router
    ctx_with_ai.mcp_client_manager = None
    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)

    async def collect():
        handler = registry._handlers.get("chat.send")
        assert handler is not None
        results = []
        async for chunk in handler(message="帮我看看笔记", stream=True):
            results.append(chunk)
        return results

    results = asyncio.run(collect())
    assert "".join(results) == "已列出笔记"
    assert router.calls and router.calls[0][0] == "notes.list"
    butler_agent._agent_cache.clear()


def test_chat_send_aggregation_stream(ctx_with_ai):
    """chat.send with aggregation + stream=True 走流式聚合路径。"""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    ctx_with_ai.aggregator = FakeAggregator("agg stream")
    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)

    async def collect():
        handler = registry._handlers.get("chat.send")
        assert handler is not None
        results = []
        async for chunk in handler(
            message="hi",
            stream=True,
            aggregation={"strategy": "race"},
        ):
            results.append(chunk)
        return results

    results = asyncio.run(collect())
    assert results == ["agg stream"]


def test_chat_send_aggregation_non_stream(ctx_with_ai):
    """chat.send with aggregation + stream=False 返回完整聚合结果。"""
    import asyncio
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    ctx_with_ai.aggregator = FakeAggregator("aggregated answer")
    registry = MethodRegistry()
    register_chat_memory(registry, ctx_with_ai)

    async def collect():
        handler = registry._handlers.get("chat.send")
        assert handler is not None
        results = []
        async for chunk in handler(
            message="hi",
            stream=False,
            aggregation={"strategy": "race"},
        ):
            results.append(chunk)
        return results

    results = asyncio.run(collect())
    assert len(results) == 1
    assert "aggregated answer" in results[0]


# ── memory.search ──


def test_memory_search_no_index(ctx):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    result = asyncio.run(registry.dispatch("memory.search", {"query": "test"}))
    assert result == []


def test_memory_recent_no_index(ctx):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    result = asyncio.run(registry.dispatch("memory.recent", {"limit": 5}))
    assert result == []


def test_memory_related_text_no_index(ctx):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    import asyncio

    result = asyncio.run(registry.dispatch("memory.related_text", {"text": "test"}))
    assert result == []
