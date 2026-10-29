# -*- coding: utf-8 -*-
"""L2 集成测试：受控进化（Evolution）状态与查询接口。

覆盖方法：evolution.status / evolution.last_briefing（可达）+ evolution.list_results /
evolution.is_redline（防复活锁）。

说明：evolution.status / evolution.last_briefing 为后端已实现但前端未调用的僵尸接口
（见 _evolution.py @deprecated），本测试仅校验接口可达且返回结构化（即便 orchestrator
为 None 也应优雅降级，而非崩溃），确保 M5 退役前的可观测性与契约稳定。
evolution.list_results / evolution.is_redline 已于波次①-c（commit 58e5fa8）下线，
下方改判为 method-not-found(-32601) 防复活锁。
"""
import pytest

pytestmark = pytest.mark.integration


async def _wrapped(rpc, method, params=None):
    resp = await rpc(method, params or {})
    assert isinstance(resp, dict) and ("result" in resp or "error" in resp), (
        f"{method} 未返回结构化 JSON-RPC 响应: {resp}"
    )
    return resp.get("result"), resp.get("error")


async def test_evolution_status_reachable(rpc):
    result, err = await _wrapped(rpc, "evolution.status")
    assert err is None, f"evolution.status 报错: {err}"
    assert isinstance(result, dict)
    assert "available" in result


async def test_evolution_last_briefing_reachable(rpc):
    result, err = await _wrapped(rpc, "evolution.last_briefing")
    assert err is None, f"evolution.last_briefing 报错: {err}"
    assert isinstance(result, dict)
    assert "briefing" in result or "error" in result


class TestEvolutionZombieRpcRemoved:
    """evolution.list_results / evolution.is_redline 已下线。

    波次①-c（commit 58e5fa8）：二者随 @deprecated 僵尸 RPC 批量下线（三重确认：
    deprecated ∩ UNUSED_BASELINE ∩ 前端零调用）摘除 registry.register，实现体仅存为
    register_evolution 内不可外部触达的闭包（无存活入口可重指向）。原「返回列表 /
    redline=False」行为用例失去被测目标，按同仓库先例 TestAgentZombieRpcRemoved
    （test_integration_agent.py）改判为断言 method-not-found(-32601) 防复活。
    """

    @pytest.mark.parametrize("method,params", [
        ("evolution.list_results", {"limit": 5}),
        ("evolution.is_redline", {"name": ""}),
    ])
    async def test_evolution_zombie_rpc_not_found(self, rpc, method, params):
        r = await rpc(method, params)
        assert r.get("error", {}).get("code") in (-32601, 32601), \
            f"{method} 应已下线（method not found），实际: {r!r}"
