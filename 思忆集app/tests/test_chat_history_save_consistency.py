"""P0-2 验证：chat.save_messages / chat.history / chat.list_conversations / chat.get_conversation
四个 RPC 必须共用同一个存储后端（chat_session_repository），且对 session_id 的字符串/数字
两种形式都能正确路由。

历史 bug：
- chat.save_messages 写到独立 chat_conversations.json（字符串 key）
- chat.history 走 chat_session_repository（int key）
→ 两者永远查不到对方的数据，对话历史功能整体失效。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from _fake_settings import configured_settings_repository

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


class _FakeDataStore:
    def __init__(self):
        self._data: dict[str, object] = {}

    def load_json(self, key: str, default=None):
        v = self._data.get(key)
        if v is None:
            return default if default is not None else {}
        return v

    def save_json(self, key: str, value):
        self._data[key] = value

    def next_id(self, sessions):
        return max((s.get("id", 0) for s in sessions), default=0) + 1


class _FakeChatRepo:
    """模拟 LocalChatSessionRepository 的最小子集，足够 P0-2 修复使用。"""

    def __init__(self):
        self._index: list[dict] = []
        self._messages: dict[int, list] = {}
        self._meta: dict[int, dict] = {}

    # 新代码用到的私有 API
    def _load_sessions_index(self) -> list[dict]:
        return list(self._index)

    def _save_sessions_index(self, sessions: list[dict]) -> None:
        self._index = list(sessions)

    def _save_meta(self, sid: int, meta: dict) -> None:
        self._meta[sid] = dict(meta)

    # 公共 API
    def list_sessions(self) -> list[dict]:
        return sorted(self._index, key=lambda s: s.get("updated_at", ""), reverse=True)

    def get_session(self, sid: int) -> dict | None:
        for s in self._index:
            if s.get("id") == sid:
                return dict(s)
        return None

    def get_messages(self, sid: int) -> list[dict]:
        return list(self._messages.get(sid, []))

    def sync_messages(self, sid: int, messages: list[dict]) -> None:
        self._messages[sid] = list(messages)
        for s in self._index:
            if s.get("id") == sid:
                s["message_count"] = len(messages)
                s["updated_at"] = "2026-06-17T00:00:00+00:00"

    def update_session(self, sid: int, info: dict) -> None:
        for s in self._index:
            if s.get("id") == sid:
                s.update(info)


class _FakeNoteRepo:
    def __init__(self):
        self.data_store = _FakeDataStore()


class _FakeContext:
    def __init__(self, chat_repo, note_repo=None):
        self.chat_session_repository = chat_repo
        self.personal_note_repository = note_repo or _FakeNoteRepo()
        self.memory_index_service = None
        self.memory_linker = None
        self.ai_client = None
        # 2026-09-18：换掉 `MagicMock() + get_setting.return_value = []`
        # （类型坍缩的 blanket 桩，详见 tests/_fake_settings.py 的动机说明）。
        self.settings_repository = configured_settings_repository()
        self.autonomous_assistant_manager = None
        self.copilot_engine = None
        self.personal_inbox_repository = None


def _build_registry(ctx):
    from backend.handlers._chat_memory import register_chat_memory
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_chat_memory(registry, ctx)
    return registry


def _call(registry, method, params):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(registry.dispatch(method, params))
    finally:
        loop.close()
        asyncio.set_event_loop(None)


# ── 端到端：save → history 应能读回相同数据 ──────────────────────────────────


def test_save_then_history_roundtrip_with_numeric_session_id():
    chat_repo = _FakeChatRepo()
    ctx = _FakeContext(chat_repo)
    registry = _build_registry(ctx)

    ok = _call(
        registry,
        "chat.save_messages",
        {
            "session_id": "42",
            "title": "今日早间",
            "messages": [
                {"speaker": "你", "message": "早安"},
                {"speaker": "小问", "message": "嗯"},
            ],
        },
    )
    assert ok is True

    msgs = _call(registry, "chat.history", {"session_id": "42"})
    assert len(msgs) == 2
    assert msgs[0]["message"] == "早安"


def test_save_then_history_roundtrip_with_uuid_session_id():
    """非数字 session_id（如 UUID）也要能稳定 hash 到 int 并双向查到。"""
    chat_repo = _FakeChatRepo()
    ctx = _FakeContext(chat_repo)
    registry = _build_registry(ctx)

    sid_raw = "ab12-cd34-ef56"
    _call(
        registry,
        "chat.save_messages",
        {
            "session_id": sid_raw,
            "title": "随机会话",
            "messages": [{"speaker": "你", "message": "测试"}],
        },
    )

    msgs = _call(registry, "chat.history", {"session_id": sid_raw})
    assert len(msgs) == 1


def test_list_conversations_returns_saved_sessions():
    chat_repo = _FakeChatRepo()
    ctx = _FakeContext(chat_repo)
    registry = _build_registry(ctx)

    _call(
        registry,
        "chat.save_messages",
        {
            "session_id": "1",
            "title": "A",
            "messages": [{"speaker": "你", "message": "1"}],
        },
    )
    _call(
        registry,
        "chat.save_messages",
        {
            "session_id": "2",
            "title": "B",
            "messages": [{"speaker": "你", "message": "2"}],
        },
    )

    convs = _call(registry, "chat.list_conversations", {})
    assert len(convs) == 2
    titles = {c["title"] for c in convs}
    assert titles == {"A", "B"}
    for c in convs:
        assert "id" in c and isinstance(c["id"], str)
        assert c["message_count"] == 1


def test_get_conversation_returns_full_data():
    chat_repo = _FakeChatRepo()
    ctx = _FakeContext(chat_repo)
    registry = _build_registry(ctx)

    _call(
        registry,
        "chat.save_messages",
        {
            "session_id": "7",
            "title": "完整",
            "messages": [{"speaker": "你", "message": "x"}],
        },
    )

    conv = _call(registry, "chat.get_conversation", {"session_id": "7"})
    assert conv is not None
    assert conv["title"] == "完整"
    assert conv["messages"] == [{"speaker": "你", "message": "x"}]


# ── 安全边界：消息数 / 单条大小 ─────────────────────────────────────────────


def test_save_rejects_too_many_messages():
    chat_repo = _FakeChatRepo()
    ctx = _FakeContext(chat_repo)
    registry = _build_registry(ctx)

    huge = [{"speaker": "你", "message": "x"}] * 5001
    ok = _call(
        registry,
        "chat.save_messages",
        {
            "session_id": "1",
            "title": "",
            "messages": huge,
        },
    )
    assert ok is False
    # 没有数据被写入
    assert chat_repo.get_messages(1) == []


def test_save_rejects_oversized_single_message():
    chat_repo = _FakeChatRepo()
    ctx = _FakeContext(chat_repo)
    registry = _build_registry(ctx)

    big = [{"speaker": "你", "message": "x" * (300 * 1024)}]
    ok = _call(
        registry,
        "chat.save_messages",
        {
            "session_id": "1",
            "title": "",
            "messages": big,
        },
    )
    assert ok is False


def test_save_rejects_empty_session_id():
    chat_repo = _FakeChatRepo()
    ctx = _FakeContext(chat_repo)
    registry = _build_registry(ctx)
    ok = _call(registry, "chat.save_messages", {"session_id": "", "messages": []})
    assert ok is False


# ── 旧数据迁移 ───────────────────────────────────────────────────────────


def test_legacy_chat_conversations_migrated_on_list():
    # 清理模块级迁移标记：其他测试用同样的 data_dir（空串）触发过迁移，
    # 标记残留会让本测试的迁移被跳过（测试隔离 bug）
    from backend.handlers import _chat_memory

    _chat_memory._LEGACY_MIGRATION_DONE.clear()

    chat_repo = _FakeChatRepo()
    note_repo = _FakeNoteRepo()
    note_repo.data_store.save_json(
        "chat_conversations",
        {
            "legacy-uuid-1": {
                "title": "旧会话",
                "messages": [{"speaker": "你", "message": "old"}],
                "updated_at": "2025-01-01T00:00:00+00:00",
            },
        },
    )
    ctx = _FakeContext(chat_repo, note_repo)
    registry = _build_registry(ctx)

    # 关键：迁移由 list_conversations / get_conversation 触发，不需要前端干预
    convs = _call(registry, "chat.list_conversations", {})
    assert len(convs) == 1
    assert convs[0]["title"] == "旧会话"
    # 旧文件已被备份并清空
    assert note_repo.data_store.load_json("chat_conversations", {}) == {}
    assert note_repo.data_store.load_json("chat_conversations.legacy", {}) != {}
    # 再次调用迁移函数无副作用（migration 标记位）
    convs2 = _call(registry, "chat.list_conversations", {})
    assert len(convs2) == 1


# ── _coerce_session_id 单元测试 ─────────────────────────────────────────


def test_coerce_session_id_variants():
    from backend.handlers._chat_memory import _coerce_session_id

    assert _coerce_session_id(42) == 42
    assert _coerce_session_id("42") == 42
    assert _coerce_session_id("0") is None
    assert _coerce_session_id(-1) is None
    assert _coerce_session_id("") is None
    assert _coerce_session_id(None) is None
    # 非数字字符串：稳定哈希到正整数
    sid_a = _coerce_session_id("abc-123")
    sid_b = _coerce_session_id("abc-123")
    assert sid_a == sid_b
    assert isinstance(sid_a, int) and sid_a > 0
    # 不同字符串映射不同
    assert _coerce_session_id("abc-123") != _coerce_session_id("xyz-789")
