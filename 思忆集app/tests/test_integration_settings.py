"""P0 集成测试：settings.get / settings.set

验证设置读写功能。
"""

import pytest

pytestmark = pytest.mark.integration


class TestSettings:
    """settings.get / settings.set"""

    async def test_get_without_key_returns_error(self, rpc):
        """U6（S3-5）：不传 key 应返回诚实的错误结构，而非伪成功提示 dict。

        旧行为返回 {"message":..., "hint":"/api/v1/settings in future"}——
        客户端无法据此判断调用成败；hint 亦是作废的 REST 迁移残留话术。
        """
        r = await rpc("settings.get", {})
        result = r.get("result", {})
        assert isinstance(result, dict)
        assert result.get("success") is False
        assert result.get("error"), "错误结构必须带 error 文案"
        assert "hint" not in result, "不得再泄露 /api/v1 迁移残留话术"

    async def test_get_with_key(self, rpc):
        """获取一个已知存在的 key"""
        r = await rpc("settings.get", {"key": "llm_model"})
        # 应返回值或 None
        assert r.get("id") is not None
        # result 可以是任意值（字符串/None/字典）

    async def test_set_and_get(self, rpc):
        """设置一个测试 key 然后读取验证"""
        test_key = "__integration_test_setting"
        test_value = "test_value_12345"

        # 1. 设置
        r_set = await rpc("settings.set", {"key": test_key, "value": test_value})
        assert r_set.get("result") is True or r_set.get("result") == 1

        # 2. 读取验证
        r_get = await rpc("settings.get", {"key": test_key})
        assert r_get.get("result") == test_value

        # 3. 清理
        await rpc("settings.set", {"key": test_key, "value": None})

    async def test_set_boolean_value(self, rpc):
        """设置布尔值"""
        test_key = "__integration_test_bool"
        r_set = await rpc("settings.set", {"key": test_key, "value": True})
        assert r_set.get("result") is True or r_set.get("result") == 1
        # 清理
        await rpc("settings.set", {"key": test_key, "value": None})

    async def test_set_dict_value(self, rpc):
        """设置字典值"""
        test_key = "__integration_test_dict"
        test_val = {"nested": {"a": 1}, "list": [1, 2, 3]}
        r_set = await rpc("settings.set", {"key": test_key, "value": test_val})
        assert r_set.get("result") is True or r_set.get("result") == 1

        r_get = await rpc("settings.get", {"key": test_key})
        assert r_get.get("result") == test_val
        # 清理
        await rpc("settings.set", {"key": test_key, "value": None})
