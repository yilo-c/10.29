# -*- coding: utf-8 -*-
"""L2 集成测试：信息雷达（Info Radar）采集与源管理。

覆盖方法：info.items.list / info.items.stats / info.items.sources /
info.sources.list / info.sources.toggle(可逆)。

设计：read-only 为主；toggle 测试在单用例内开启→关闭（或反向）做可逆校验，
不影响全局源配置的最终状态。info.items.* 由 info_collector_service 实时采集，
后端启动后应有数据，断言 items 为列表即可（允许为空）。
"""
import pytest

pytestmark = pytest.mark.integration


async def _wrapped(rpc, method, params=None):
    resp = await rpc(method, params or {})
    assert isinstance(resp, dict) and ("result" in resp or "error" in resp), (
        f"{method} 未返回结构化 JSON-RPC 响应: {resp}"
    )
    return resp.get("result"), resp.get("error")


async def test_info_items_list_reachable(rpc):
    result, err = await _wrapped(rpc, "info.items.list", {"page": 1, "page_size": 10})
    assert err is None, f"info.items.list 报错: {err}"
    assert isinstance(result, dict)
    assert "items" in result or "error" in result
    if "items" in result:
        assert isinstance(result["items"], list)


async def test_info_items_stats_reachable(rpc):
    result, err = await _wrapped(rpc, "info.items.stats", {})
    assert err is None, f"info.items.stats 报错: {err}"
    assert isinstance(result, dict)
    assert "stats" in result or "error" in result
    if "stats" in result:
        assert isinstance(result["stats"], dict)


async def test_info_items_sources_reachable(rpc):
    result, err = await _wrapped(rpc, "info.items.sources", {})
    assert err is None, f"info.items.sources 报错: {err}"
    assert isinstance(result, dict)
    assert "sources" in result or "error" in result
    if "sources" in result:
        assert isinstance(result["sources"], list)


async def test_info_sources_list_reachable(rpc):
    result, err = await _wrapped(rpc, "info.sources.list", {"page": 1, "page_size": 20})
    assert err is None, f"info.sources.list 报错: {err}"
    assert isinstance(result, dict)
    assert "sources" in result or "error" in result
    if "sources" in result:
        assert isinstance(result["sources"], list)


async def test_info_sources_toggle_reversible(rpc):
    lst, err = await _wrapped(rpc, "info.sources.list", {"page": 1, "page_size": 50})
    assert err is None, f"info.sources.list 报错: {err}"
    sources = lst.get("sources") if isinstance(lst, dict) else None
    if not sources:
        pytest.skip("无可用信息源，跳过 toggle 可逆性测试")
    name = sources[0].get("name") or sources[0].get("id")
    orig_enabled = bool(sources[0].get("enabled"))
    flipped, err1 = await _wrapped(rpc, "info.sources.toggle", {"name": name, "enabled": not orig_enabled})
    assert err1 is None, f"info.sources.toggle 报错: {err1}"
    assert isinstance(flipped, dict)
    assert flipped.get("success") is True
    restored, err2 = await _wrapped(rpc, "info.sources.toggle", {"name": name, "enabled": orig_enabled})
    assert err2 is None, f"info.sources.toggle 还原报错: {err2}"
    assert isinstance(restored, dict)
    assert restored.get("success") is True
