# -*- coding: utf-8 -*-
"""S0-5（八轮实测 AJ-1）回归：tool.* 全族 handler 的 router 解析链。

旧链 ``_get_router()`` 读 context.autonomy_layer / context.autonomy——两属性
全仓从未挂载（facade 白名单只有 autonomous_assistant_manager），导致
tool.register / tool.list / tool.unregister / tool.health_check 恒
"tool registry not available"。

修复后与 assistant/agents/butler_agent.py:_resolve_capability_router 对齐：
真实链为 context.autonomous_assistant_manager.router
（AssistantCapabilityRouter，其上挂 tool_registry，见
assistant/capability_router.py:240-252；manager 注入见
app_runtime/_context_builder.py:446，router 注入见
assistant/autonomy_components/lifecycle_builder.py:61）。
"""

from __future__ import annotations

from backend.handlers._tools import register_tools


class _RpcRegistry:
    """捕获 registry.register(name, fn) 的假 JSON-RPC 注册表。"""

    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def register(self, name: str, fn) -> None:
        self.handlers[name] = fn


async def _call_tool_list(ctx) -> dict:
    """注册 handlers 并直接 await tool.list，返回其结果。"""
    reg = _RpcRegistry()
    register_tools(reg, ctx)
    return await reg.handlers["tool.list"]()


class _FakeToolRegistry:
    def __init__(self) -> None:
        self.list_calls = 0

    def list_specs(self):
        self.list_calls += 1
        return []


class _FakeRouter:
    def __init__(self, tool_registry) -> None:
        self.tool_registry = tool_registry


class _FakeRouterNoRegistry:
    """没有 tool_registry 属性的 router——应被 hasattr 校验拒掉。"""


class _FakeManager:
    def __init__(self, router) -> None:
        self.router = router


class _Ctx:
    def __init__(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


# 真实链：autonomous_assistant_manager.router.tool_registry
async def test_tool_list_resolves_via_autonomous_assistant_manager() -> None:
    tr = _FakeToolRegistry()
    ctx = _Ctx(autonomous_assistant_manager=_FakeManager(_FakeRouter(tr)))
    out = await _call_tool_list(ctx)
    assert out["success"] is True, f"应经真实链取到 router，实际：{out}"
    assert out["tools"] == [] and out["total"] == 0
    assert tr.list_calls == 1, "tool_registry 未被触达"


# 兼容兜底：context.capability_router 直挂（测试 harness 形态）
async def test_tool_list_falls_back_to_direct_capability_router() -> None:
    tr = _FakeToolRegistry()
    ctx = _Ctx(capability_router=_FakeRouter(tr))
    out = await _call_tool_list(ctx)
    assert out["success"] is True, f"capability_router 兜底失效：{out}"
    assert tr.list_calls == 1


# 死案复现防护：旧的 autonomy_layer / autonomy 属性不再被采信
async def test_old_dead_attrs_no_longer_honored() -> None:
    tr = _FakeToolRegistry()
    ctx = _Ctx(autonomy_layer=_FakeRouter(tr), autonomy=_FakeRouter(tr))
    out = await _call_tool_list(ctx)
    assert out["success"] is False
    assert out["error"] == "tool registry not available"
    assert tr.list_calls == 0, "旧死链属性不应再被 _get_router 采信"


# manager 在但 router 无 tool_registry → 不误取
async def test_router_without_tool_registry_rejected() -> None:
    ctx = _Ctx(autonomous_assistant_manager=_FakeManager(_FakeRouterNoRegistry()))
    out = await _call_tool_list(ctx)
    assert out["success"] is False
    assert out["error"] == "tool registry not available"


# 全空 context：维持安全失败
async def test_empty_context_stays_unavailable() -> None:
    out = await _call_tool_list(_Ctx())
    assert out["success"] is False
    assert out["error"] == "tool registry not available"
