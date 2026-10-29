# -*- coding: utf-8 -*-
"""L2 集成测试：时间线（Timeline）聚合查询。

覆盖方法：timeline.list / timeline.stats。

设计：read-only 聚合接口，断言返回结构包含 entries 列表与统计字段。
允许 entries 为空（新实例无事件），重点是契约稳定与可分页。
"""
import pytest

pytestmark = pytest.mark.integration


async def _wrapped(rpc, method, params=None):
    resp = await rpc(method, params or {})
    assert isinstance(resp, dict) and ("result" in resp or "error" in resp), (
        f"{method} 未返回结构化 JSON-RPC 响应: {resp}"
    )
    return resp.get("result"), resp.get("error")


async def test_timeline_list_reachable(rpc):
    result, err = await _wrapped(rpc, "timeline.list", {"limit": 20, "offset": 0})
    assert err is None, f"timeline.list 报错: {err}"
    assert isinstance(result, dict)
    assert "entries" in result or "error" in result
    if "entries" in result:
        assert isinstance(result["entries"], list)
        assert "count" in result or "total" in result or "next_cursor" in result


async def test_timeline_stats_reachable(rpc):
    result, err = await _wrapped(rpc, "timeline.stats", {})
    assert err is None, f"timeline.stats 报错: {err}"
    assert isinstance(result, dict)
    assert "total" in result or "error" in result
    if "total" in result:
        assert isinstance(result["total"], int)
