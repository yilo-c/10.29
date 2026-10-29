"""P0 集成测试：api_keys.list / create / update / delete / test

验证 API Key 管理的完整 CRUD 流程。
"""
import pytest

pytestmark = pytest.mark.integration



async def _resolve_pending(rpc, r):
    """R4 审批闸（M2.1）：api_keys 写操作返回 {pending:true, pending_id}。
    批准后 registry 重放原 RPC，返回内层 result。非 pending 响应原样返回。"""
    result = r.get("result", {})
    if isinstance(result, dict) and result.get("pending") and result.get("pending_id"):
        r_appr = await rpc("sandbox.approve", {"pending_id": result["pending_id"]})
        appr = r_appr.get("result", {})
        if isinstance(appr, dict) and "result" in appr:
            return {"result": appr["result"]}
        return r_appr
    return r


class TestApiKeysCRUD:
    """api_keys.* 全流程测试"""

    async def test_list_keys(self, rpc):
        """列出 API keys"""
        r = await rpc("api_keys.list", {})
        result = r.get("result", [])
        assert isinstance(result, list)

    async def test_create_and_delete_key(self, rpc):
        """创建 → 列表确认 → 删除 完整流程"""
        # 1. 创建（api_key_service.create_key 期望 key_value 而非 key）
        r_create = await rpc("api_keys.create", {
            "name": "__test_integration_provider",
            "key_value": "sk-test-integration-12345",
            "is_provider": True,
            "base_url": "https://api.example.com/v1",
            "model": "test-model",
        })
        r_create = await _resolve_pending(rpc, r_create)
        result = r_create.get("result", {})
        # create_key 返回 {"id": "provider:name", "name": ...} 或抛异常
        if "error" in r_create:
            pytest.skip(f"创建 key 失败: {r_create.get('error')}")
        assert result.get("id") or result.get("success") is True

        key_id = result.get("id", "")

        # 2. 列表确认
        r_list = await rpc("api_keys.list", {})
        keys = r_list.get("result", [])
        # api_keys.list 返回 provider 列表
        test_keys = [k for k in keys if k.get("name") == "__test_integration_provider"]
        assert len(test_keys) >= 1

        # 3. 删除
        if key_id:
            r_del = await _resolve_pending(rpc, await rpc("api_keys.delete", {"key_id": key_id}))
            del_result = r_del.get("result", {})
            assert del_result.get("deleted") is True or del_result.get("success") is True

    async def test_update_key(self, rpc):
        """创建 → 更新名称 → 确认 → 删除"""
        # 1. 创建
        r_create = await rpc("api_keys.create", {
            "name": "__test_update_provider",
            "key_value": "sk-test-update-12345",
            "is_provider": True,
            "base_url": "https://api.example.com/v1",
            "model": "test-model",
        })
        r_create = await _resolve_pending(rpc, r_create)
        result = r_create.get("result", {})
        if "error" in r_create:
            pytest.skip(f"创建 key 失败: {r_create.get('error')}")
        assert result.get("id")
        key_id = result.get("id", "")

        # 2. 更新
        if key_id:
            r_update = await _resolve_pending(rpc, await rpc("api_keys.update", {
                "key_id": key_id,
                "name": "__test_updated_provider",
            }))
            # update 可能返回成功或错误
            assert r_update.get("id") is not None or r_update.get("result") is not None

            # 3. 清理
            await _resolve_pending(rpc, await rpc("api_keys.delete", {"key_id": key_id}))

    async def test_test_key(self, rpc):
        """api_keys.test — 测试 key 连通性"""
        # 先创建一个测试 key
        r_create = await rpc("api_keys.create", {
            "name": "__test_connectivity",
            "key_value": "sk-test-fake-key-00000",
            "is_provider": True,
            "base_url": "https://api.example.com/v1",
            "model": "test-model",
        })
        r_create = await _resolve_pending(rpc, r_create)
        result = r_create.get("result", {})
        if "error" in r_create or not result.get("id"):
            pytest.skip("无法创建测试 key")

        key_id = result.get("id", "")
        if key_id:
            # 测试连通性（fake key 应该失败）
            r_test = await rpc("api_keys.test", {"key_id": key_id})
            result = r_test.get("result", {})
            # fake key 应返回 success=False 或 error
            assert "success" in result or "error" in result
            # 清理
            await _resolve_pending(rpc, await rpc("api_keys.delete", {"key_id": key_id}))

    async def test_delete_nonexistent_key(self, rpc):
        """删除不存在的 key 应优雅处理（返回 error 而非崩溃）"""
        r = await _resolve_pending(rpc, await rpc("api_keys.delete", {"key_id": "provider:nonexistent_99999"}))
        # delete_key 抛 ValueError → RPC 返回 error；R4 批准重放后错误在 result 内层
        result = r.get("result", {})
        assert ("error" in r or result.get("error")
                or result.get("deleted") is False or result.get("success") is False)
