"""P0 集成测试：remote.agent.execute

验证手机端远程控制桌面端的安全边界。

V2.1 批次1#7: remote.shell / remote.screenshot 已移除（攻击面净收缩），
其测试类同步删除；此处断言二者不再注册（返回 method-not-found）。
"""

import pytest

pytestmark = pytest.mark.integration


class TestRemoteRemovedAttackSurface:
    """V2.1 批次1#7: remote.shell / remote.screenshot 已被移除"""

    async def test_remote_shell_not_registered(self, rpc):
        """remote.shell 应不再存在（method not found）"""
        r = await rpc("remote.shell", {"command": "echo"})
        assert r.get("error", {}).get("code") in (-32601, 32601) or "not" in str(r)

    async def test_remote_screenshot_not_registered(self, rpc):
        """remote.screenshot 应不再存在（method not found）"""
        r = await rpc("remote.screenshot", {})
        assert r.get("error", {}).get("code") in (-32601, 32601) or "not" in str(r)


class TestRemoteAgentExecute:
    """remote.agent.execute — 已随波次①-c（commit 58e5fa8）下线。

    实现体 remote_agent_execute 仅存为 register_remote 内不可外部触达的闭包（无存活
    入口可重指向），原参数校验行为用例失去被测目标，按上方
    TestRemoteRemovedAttackSurface 先例改为断言 method-not-found(-32601) 防复活。
    """

    async def test_remote_agent_execute_not_registered(self, rpc):
        r = await rpc("remote.agent.execute", {"agent_id": "claude_code", "task": "test"})
        assert r.get("error", {}).get("code") in (-32601, 32601) or "not" in str(r), \
            f"remote.agent.execute 应已下线（method not found），实际: {r!r}"
