"""Chat session smoke tests: create, append message, close/reopen preserves data."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from repositories._chat_session import LocalChatSessionRepository


def test_chat_create_message_and_reopen(tmp_path):
    """Create a session, add a message, close/reopen — message survives."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalChatSessionRepository(store)

    session_id = repo.create_session()  # default title "新对话"
    repo.add_message(session_id, "你", "你好")
    repo.add_message(session_id, "助手", "你好！有浠€么可以帮助你的？")

    # Simulate close
    saved_id = session_id
    del repo
    del store

    # Reopen
    store2 = LocalDataStore(base_dir=str(tmp_path))
    repo2 = LocalChatSessionRepository(store2)

    session = repo2.get_session(saved_id)
    assert session is not None, "会话应保鐣"
    assert session["title"] == "你好", "标题应从首条用户消息自动生成"
    messages = repo2.get_messages(saved_id)
    assert len(messages) == 2
    assert messages[0]["speaker"] == "你"
    assert messages[0]["message"] == "你好"
    assert messages[1]["speaker"] == "助手"


def test_chat_session_crud(tmp_path):
    """Create, list, update, delete session."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalChatSessionRepository(store)

    s1 = repo.create_session("对话A")
    s2 = repo.create_session("对话B")
    assert len(repo.list_sessions()) == 2

    repo.update_session(s1, {"title": "对话A-更新"})
    assert repo.get_session(s1)["title"] == "对话A-更新"

    repo.delete_session(s2)
    assert len(repo.list_sessions()) == 1
    assert repo.get_session(s2) is None


def test_legacy_inline_messages_migrated_on_access(tmp_path):
    """Sessions with old inline ``messages`` key migrate to segments on first get_messages()."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalChatSessionRepository(store)

    # Simulate an old-style session with inline messages (no segments)
    sessions = repo._load_sessions_index()
    sessions.append({
        "id": 99,
        "title": "旧会话",
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
        "messages": [
            {"speaker": "你", "message": "旧消息", "timestamp": "2025-01-01T00:00:00+00:00"},
            {"speaker": "助手", "message": "旧回复", "timestamp": "2025-01-01T00:00:00+00:00"},
            {"speaker": "你", "message": "旧消息", "timestamp": "2025-01-01T00:00:00+00:00"},
        ],
    })
    repo._save_sessions_index(sessions)

    # Access via get_messages — should migrate
    msgs = repo.get_messages(99)
    assert len(msgs) == 3
    assert msgs[0]["message"] == "旧消息"
    assert msgs[2]["message"] == "旧消息"

    # After migration, inline messages should be gone
    migrated = repo.get_session(99)
    assert migrated is not None
    assert "messages" not in migrated, "inline messages should be removed after migration"
    assert migrated["message_count"] == 3

    # Reopen 鈥segments should persist
    store2 = LocalDataStore(base_dir=str(tmp_path))
    repo2 = LocalChatSessionRepository(store2)
    msgs2 = repo2.get_messages(99)
    assert len(msgs2) == 3


def test_segment_roundtrip_many_messages(tmp_path):
    """Creating more than CHUNK_SIZE messages correctly spans multiple frozen chunks + hot tail."""
    from repositories._chat_session import CHUNK_SIZE
    n = CHUNK_SIZE * 3 + 7  # 3 frozen chunks + hot tail of 7

    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalChatSessionRepository(store)
    sid = repo.create_session("大会话")

    messages = []
    for i in range(n):
        messages.append({"speaker": "你" if i % 2 == 0 else "助手",
                         "message": f"消息{i}", "timestamp": "2025-01-01T00:00:00+00:00"})
    repo.sync_messages(sid, messages)

    loaded = repo.get_messages(sid)
    assert len(loaded) == n
    assert loaded[0]["message"] == "消息0"
    assert loaded[-1]["message"] == f"消息{n-1}"

    # Verify chunk_count in session metadata
    s = repo.get_session(sid)
    assert s is not None
    assert s["chunk_count"] == 3  # 3 frozen chunks

    # Reopen
    store2 = LocalDataStore(base_dir=str(tmp_path))
    repo2 = LocalChatSessionRepository(store2)
    loaded2 = repo2.get_messages(sid)
    assert len(loaded2) == n
    assert loaded2[-1]["message"] == f"消息{n-1}"
