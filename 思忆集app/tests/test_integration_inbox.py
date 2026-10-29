"""P1 集成测试：inbox.* 全部 4 个方法

验证收件箱 CRUD + AI 分类。
"""

import pytest

pytestmark = pytest.mark.integration


class TestInboxCRUD:
    """inbox.list / add / update / classify"""

    async def test_list_inbox(self, rpc):
        r = await rpc("inbox.list", {"include_archived": False})
        result = r.get("result", [])
        assert isinstance(result, list)

    async def test_add_and_list(self, rpc):
        """添加 → 列表确认 → 清理"""
        # 1. 添加
        r_add = await rpc("inbox.add", {"content": "__integration_test_inbox_entry"})
        result = r_add.get("result", {})
        entry_id = result.get("id", "")
        assert entry_id != ""

        # 2. 等待 AI 分类完成（避免分类任务阻塞后续 RPC）
        import asyncio

        await asyncio.sleep(2)

        # 3. 列表确认
        # 2026-09-17：inbox.list 现默认 limit=50 + order=asc（分页收口批次）。
        # 旧断言依赖"全量返回"，隔离库条目沉淀超 50 条后新条目排在尾页永远读不到
        # ——测试法过时而非产品缺陷。改 desc（用户加完新条目期望最新在前，与真实
        # 前端行为一致）并显式 limit，读回自身写入的条目。
        r_list = await rpc("inbox.list", {"include_archived": True, "order": "desc", "limit": 200})
        entries = r_list.get("result", [])
        found = [e for e in entries if e.get("id") == entry_id]
        assert len(found) >= 1, f"新条目 id={entry_id} 未在列表中找到（共 {len(entries)} 条）"

        # 3. 更新（归档）
        r_update = await rpc(
            "inbox.update",
            {
                "inbox_id": entry_id,
                "archived": True,
            },
        )
        assert r_update.get("id") is not None

    async def test_update_nonexistent(self, rpc):
        """更新不存在的条目应优雅处理"""
        r = await rpc("inbox.update", {"inbox_id": "nonexistent_99999", "content": "test"})
        result = r.get("result", {})
        assert "error" in result or "success" in result or result is None

    async def test_classify_nonexistent(self, rpc):
        """分类不存在的条目应优雅处理"""
        r = await rpc("inbox.classify", {"entry_id": "nonexistent_99999"})
        result = r.get("result", {})
        assert "error" in result or "success" in result or result is None
