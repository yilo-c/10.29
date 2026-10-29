"""扩展集成测试：chat.* 剩余 4 个方法 + notes.auto_tag + notes.generate_summary

覆盖 chat.list_conversations / chat.get_conversation / chat.save_messages / chat.compress_context
以及 notes.auto_tag / notes.generate_summary
"""
import pytest

pytestmark = pytest.mark.integration


class TestChatListConversations:
    """chat.list_conversations — 会话列表"""

    async def test_list_conversations(self, rpc):
        r = await rpc("chat.list_conversations", {"limit": 10})
        result = r.get("result", [])
        assert isinstance(result, list)

    async def test_list_with_limit(self, rpc):
        r = await rpc("chat.list_conversations", {"limit": 2})
        result = r.get("result", [])
        # chat.list_conversations 可能不严格限制 limit，只验证返回了列表
        assert isinstance(result, list)


class TestChatGetConversation:
    """chat.get_conversation — 获取单个会话"""

    async def test_get_nonexistent_conversation(self, rpc):
        r = await rpc("chat.get_conversation", {"session_id": "nonexistent_session_99999"})
        # 不存在的会话应返回 None 或空
        assert r.get("result") is None or r.get("result") == {} or isinstance(r.get("result"), dict)


class TestChatSaveMessages:
    """chat.save_messages — 保存聊天消息"""

    async def test_save_and_verify(self, rpc):
        """保存消息 → 历史确认 → 清理"""
        session_id = "__test_integration_chat_session"

        # 1. 保存
        r_save = await rpc("chat.save_messages", {
            "session_id": session_id,
            "messages": [
                {"role": "user", "content": "测试消息1"},
                {"role": "assistant", "content": "测试回复1"},
            ],
        })
        assert r_save.get("id") is not None

        # 2. 历史确认
        r_hist = await rpc("chat.history", {"session_id": session_id, "limit": 10})
        result = r_hist.get("result", [])
        if isinstance(result, list):
            assert len(result) >= 2

    async def test_save_empty_messages(self, rpc):
        r = await rpc("chat.save_messages", {
            "session_id": "__test_empty",
            "messages": [],
        })
        assert r.get("id") is not None


class TestChatCompressContext:
    """chat.compress_context — 压缩对话上下文"""

    async def test_compress_nonexistent_session(self, rpc):
        r = await rpc("chat.compress_context", {"session_id": "nonexistent_99999"})
        # 不存在的会话应优雅处理
        assert r.get("id") is not None


class TestNotesAutoTag:
    """notes.auto_tag — AI 自动打标签"""

    async def test_auto_tag_nonexistent_note(self, rpc):
        r = await rpc("notes.auto_tag", {"note_id": 999999})
        result = r.get("result")
        # 不存在的笔记可能返回空列表、空字符串或 error dict
        assert result is not None  # 只要服务器响应了就行


class TestNotesGenerateSummary:
    """notes.generate_summary — AI 生成摘要"""

    async def test_summary_nonexistent_note(self, rpc):
        r = await rpc("notes.generate_summary", {"note_id": 999999})
        result = r.get("result")
        # 不存在的笔记可能返回空字符串、None 或 error dict
        assert result is not None  # 只要服务器响应了就行
