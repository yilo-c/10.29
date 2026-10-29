"""P1 集成测试：reading.* 全部 7 个方法

验证阅读位置同步和稍后读文章功能。
"""

import pytest

pytestmark = pytest.mark.integration


class TestReadingPosition:
    """reading.push / get / list / delete"""

    async def test_push_and_get(self, rpc_approved):
        """推送 → 获取 → 删除 完整流程（reading.delete 为 R4 → 审批后执行）"""
        note_id = "__test_reading_integration"

        # 1. push
        r_push = await rpc_approved(
            "reading.push",
            {
                "note_id": note_id,
                "scroll_position": 500,
                "percentage": 42.5,
                "device_id": "test_mobile",
            },
        )
        result = r_push.get("result", {})
        assert result.get("success") is True or "note_id" in result

        # 2. get
        r_get = await rpc_approved("reading.get", {"note_id": note_id})
        result = r_get.get("result", {})
        if result:
            assert result.get("note_id") == note_id
            assert result.get("scroll_position") == 500

        # 3. delete
        r_del = await rpc_approved("reading.delete", {"note_id": note_id})
        result = r_del.get("result", {})
        assert result.get("success") is True

    async def test_push_missing_note_id(self, rpc):
        r = await rpc("reading.push", {"scroll_position": 100})
        result = r.get("result", {})
        assert "error" in result

    async def test_get_nonexistent(self, rpc):
        r = await rpc("reading.get", {"note_id": "nonexistent_99999"})
        # 不存在的应返回 None 或空
        assert r.get("result") is None or r.get("result") == {}

    async def test_list_positions(self, rpc):
        r = await rpc("reading.list", {})
        result = r.get("result", {})
        assert isinstance(result, dict)
        assert "positions" in result or "count" in result

    async def test_delete_nonexistent(self, rpc_approved):
        r = await rpc_approved("reading.delete", {"note_id": "nonexistent_99999"})
        result = r.get("result", {})
        # rpc_approved 返回 approve 响应：执行结果嵌套在 result.result
        inner = result.get("result") if isinstance(result, dict) else None
        target = inner if isinstance(inner, dict) else result
        assert target.get("success") is False or "error" in target


class TestReadingArticles:
    """reading.save_article / list_articles / delete_article"""

    async def test_save_and_list_article(self, rpc_approved):
        """保存 → 列表 → 删除 完整流程（reading.delete_article 为 R4 → 审批后执行）"""
        # 1. 保存
        r_save = await rpc_approved(
            "reading.save_article",
            {
                "url": "https://example.com/test-integration",
                "title": "__test_integration_article",
                "content": "This is integration test content.",
            },
        )
        result = r_save.get("result", {})
        assert result.get("success") is True or "id" in result or "article_id" in result

        # 2. 列表
        r_list = await rpc_approved("reading.list_articles", {"limit": 20})
        result = r_list.get("result", {})
        assert isinstance(result, dict) or isinstance(result, list)
        # 找到测试文章
        if isinstance(result, dict):
            articles = result.get("articles", [])
        else:
            articles = result
        test_articles = [a for a in articles if a.get("title") == "__test_integration_article"]
        assert len(test_articles) >= 1

        # 3. 删除
        article_id = test_articles[0].get("id", test_articles[0].get("article_id", ""))
        if article_id:
            r_del = await rpc_approved("reading.delete_article", {"article_id": article_id})
            assert r_del.get("id") is not None

    async def test_list_articles(self, rpc_approved):
        r = await rpc_approved("reading.list_articles", {"limit": 5})
        assert r.get("id") is not None

    async def test_delete_nonexistent_article(self, rpc_approved):
        r = await rpc_approved("reading.delete_article", {"article_id": "nonexistent_99999"})
        result = r.get("result", {})
        assert "error" in result or "success" in result
