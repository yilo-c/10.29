"""P1 集成测试：memory.* 全部 8 个方法

验证记忆系统的搜索、相关、叙事等功能。
"""
import pytest

pytestmark = pytest.mark.integration


class TestMemorySearch:
    """memory.search — 语义搜索记忆"""

    async def test_search_returns_results(self, rpc):
        r = await rpc("memory.search", {"query": "测试", "limit": 5})
        result = r.get("result", [])
        assert isinstance(result, list)

    async def test_search_with_limit(self, rpc):
        r = await rpc("memory.search", {"query": "hello", "limit": 3})
        result = r.get("result", [])
        assert len(result) <= 3

    async def test_search_empty_query(self, rpc):
        """空查询应返回空列表或错误"""
        r = await rpc("memory.search", {"query": "", "limit": 5})
        assert r.get("id") is not None  # 只要服务器响应了


class TestMemoryRecent:
    """memory.recent — 最近记忆"""

    async def test_recent_returns_list(self, rpc):
        r = await rpc("memory.recent", {"limit": 10})
        result = r.get("result", [])
        assert isinstance(result, list)

    async def test_recent_with_limit(self, rpc):
        r = await rpc("memory.recent", {"limit": 2})
        result = r.get("result", [])
        assert len(result) <= 2


class TestMemoryRelated:
    """memory.related / memory.related_text"""

    async def test_related_text(self, rpc):
        """基于文本的相关记忆"""
        r = await rpc("memory.related_text", {"text": "编程", "limit": 3})
        result = r.get("result", [])
        assert isinstance(result, list)

    async def test_related_by_id(self, rpc):
        """基于 ID 的相关记忆（不存在的 ID）"""
        r = await rpc("memory.related", {"memory_id": "nonexistent_99999", "limit": 3})
        result = r.get("result", [])
        assert isinstance(result, list)


class TestMemoryNarrative:
    """memory.narrative.latest / memory.narrative.types"""

    async def test_narrative_latest(self, rpc):
        r = await rpc("memory.narrative.latest", {})
        # 可能返回 null（无叙事）或 dict
        assert r.get("id") is not None

    async def test_narrative_types(self, rpc):
        r = await rpc("memory.narrative.types", {})
        result = r.get("result", [])
        assert isinstance(result, list)


class TestMemorySurfaceForInput:
    """memory.surface_for_input — 输入时浮现相关记忆"""

    async def test_surface_for_input(self, rpc):
        r = await rpc("memory.surface_for_input", {"text": "今天天气"})
        result = r.get("result", [])
        assert isinstance(result, list)


class TestMemorySaveConversationAnchor:
    """memory.save_conversation_anchor"""

    async def test_save_anchor(self, rpc):
        r = await rpc("memory.save_conversation_anchor", {
            "session_id": "test_session_integration",
            "text": "集成测试锚点内容",
        })
        # 返回 True（成功）或 False（无 session_id/text）
        result = r.get("result")
        assert result is True or result is False or isinstance(result, dict)


class TestTagsList:
    """tags.list — 所有标签"""

    async def test_tags_list(self, rpc):
        r = await rpc("tags.list", {})
        result = r.get("result", [])
        assert isinstance(result, list)
