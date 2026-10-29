"""P0 集成测试：agent.list / agent.schedule / agent.cancel / agent.run_now / agent.history

验证 Agent 编排系统的端到端流程。
"""
import pytest

pytestmark = pytest.mark.integration


class TestAgentList:
    """agent.list — 列出已注册 Agent"""

    async def test_list_returns_agents(self, rpc):
        """应返回 agents 列表（可能为空）"""
        r = await rpc("agent.list", {})
        result = r.get("result", {})
        assert result.get("success") is True or "agents" in result
        agents = result.get("agents", [])
        assert isinstance(agents, list)

    async def test_list_has_count(self, rpc):
        """返回应包含 count 字段"""
        r = await rpc("agent.list", {})
        result = r.get("result", {})
        assert "count" in result or "agents" in result


class TestAgentZombieRpcRemoved:
    """agent.schedule / agent.cancel / agent.run_now / agent.history 已下线。

    波次①-c（commit 58e5fa8）：四个 @deprecated 僵尸 RPC 随三重确认（deprecated ∩
    UNUSED_BASELINE ∩ 前端零调用）摘除注册，实现体仅存为 register_agent_tasks 内不可
    外部触达的闭包（无存活入口可重指向）。原参数校验行为用例失去被测目标，
    按同仓库先例 TestRemoteRemovedAttackSurface（test_integration_remote.py）改为
    断言 method-not-found(-32601) 防复活。
    """

    @pytest.mark.parametrize("method", [
        "agent.schedule",
        "agent.cancel",
        "agent.run_now",
        "agent.history",
    ])
    async def test_agent_zombie_rpc_not_found(self, rpc, method):
        r = await rpc(method, {"agent_id": "claude_code", "task": "test", "job_id": "j1", "limit": 5})
        assert r.get("error", {}).get("code") in (-32601, 32601) or "not" in str(r), \
            f"{method} 应已下线（method not found），实际: {r!r}"
