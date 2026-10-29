"""审批（R4 流）决议链路端到端测试。

P0-6 已覆盖：未审批 R4 dispatch 返回 pending 且不执行；skip_approval 重放执行。
本文件补覆盖**真实的 sandbox.approve 决议路径**（此前 0% 覆盖）：
  dispatch(R4) → sandbox.add(kind="rpc") 入队 → sandbox.approve(pending_id)
  → registry.dispatch(action_id, args, skip_approval=True) 真正执行。
这是「破坏性操作须经用户审批确认后才放行」设计契约的闭环验证。
隔离：纯内存 MagicMock context + 自管 R4 方法，不依赖真实后端/文件系统。

审批策略任务 #3 增补（TestSandboxHandlerBoundaries）：handler 层防御边界——
sandbox.preview 白名单校验、kind="rpc" 批准缺 registry 时 failed 且不执行。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.handlers import _sandbox, register_all
from backend.method_registry import RISK_R4, MethodRegistry


class _CollectingContext:
    def __init__(self) -> None:
        self.local_data_store = SimpleNamespace(base_dir="/tmp/siyi-r4-res-test")
        self.settings_repository = MagicMock()


@pytest.fixture
async def reg_with_sandbox() -> MethodRegistry:
    reg = MethodRegistry()
    ctx = _CollectingContext()
    await register_all(reg, ctx, ws_port=18765)
    executed: list[str] = []

    async def r4_demo(value: str = "", **_kwargs) -> dict:
        executed.append(value)
        return {"ok": True, "value": value}

    # 暴露 executed 以便断言；用 registry 的自定义属性挂载
    reg._demo_executed = executed  # type: ignore[attr-defined]
    reg.register("test.r4_demo", r4_demo, risk=RISK_R4)
    return reg


@pytest.mark.asyncio
async def test_r4_dispatch_then_approve_executes(reg_with_sandbox: MethodRegistry) -> None:
    """完整决议闭环：未审批入队 → approve 后真正执行 handler。"""
    res = await reg_with_sandbox.dispatch("test.r4_demo", {"value": "V1"})
    assert res.get("pending") is True, "R4 未审批应入队 pending"
    pid = res["pending_id"]

    approve = reg_with_sandbox._handlers["sandbox.approve"]
    out = await approve(pending_id=pid)
    assert out.get("success") is True, f"approve 应成功执行: {out}"
    assert out.get("action_id") == "test.r4_demo"
    assert reg_with_sandbox._demo_executed == ["V1"], "approve 后 handler 未执行"


@pytest.mark.asyncio
async def test_r4_dispatch_then_reject_not_executed(reg_with_sandbox: MethodRegistry) -> None:
    """reject 路径：拒绝后 handler 不执行，pending 被丢弃。"""
    res = await reg_with_sandbox.dispatch("test.r4_demo", {"value": "V2"})
    pid = res["pending_id"]

    reject = reg_with_sandbox._handlers["sandbox.reject"]
    out = await reject(pending_id=pid)
    assert out.get("status") in ("rejected", "ok", None) or "reject" in str(out).lower()

    assert reg_with_sandbox._demo_executed == [], "reject 后 handler 不应执行"


@pytest.mark.asyncio
async def test_r4_list_pending_contains_entry(reg_with_sandbox: MethodRegistry) -> None:
    """list_pending 应能看到刚入队的 R4 待审批条目。"""
    res = await reg_with_sandbox.dispatch("test.r4_demo", {"value": "V3"})
    pid = res["pending_id"]

    list_pending = reg_with_sandbox._handlers["sandbox.list_pending"]
    pending = await list_pending()
    ids = {p.get("pending_id") for p in pending}
    assert pid in ids, "list_pending 未包含刚入队的 R4 条目"


@pytest.mark.asyncio
async def test_approve_unknown_pending_id_errors(reg_with_sandbox: MethodRegistry) -> None:
    """approve 未知 pending_id 应优雅报错（不执行、不崩溃）。"""
    approve = reg_with_sandbox._handlers["sandbox.approve"]
    out = await approve(pending_id="does-not-exist")
    assert out.get("success") is not True
    assert "不存在" in out.get("error", "")
    assert reg_with_sandbox._demo_executed == []


class TestSandboxHandlerBoundaries:
    """handler 层防御边界（审批策略任务 #3 增补）。"""

    @pytest.mark.asyncio
    async def test_preview_requires_action_id(self) -> None:
        out = await _sandbox._handle_sandbox_preview(action_id="")
        assert "error" in out

    @pytest.mark.asyncio
    async def test_preview_rejects_non_allowlisted_action(self) -> None:
        """sandbox.preview 仅支持 file.write / file.copy，其余 action 一律拒绝。"""
        out = await _sandbox._handle_sandbox_preview(action_id="notes.delete")
        assert "不支持" in str(out.get("error", ""))

    @pytest.mark.asyncio
    async def test_rpc_approve_without_registry_marks_failed(self, reg_with_sandbox: MethodRegistry) -> None:
        """防御分支：kind="rpc" 条目批准时缺 registry 引用 → failed 且 handler 不执行。"""
        res = await reg_with_sandbox.dispatch("test.r4_demo", {"value": "V4"})
        pid = res["pending_id"]

        out = await _sandbox._handle_sandbox_approve(pending_id=pid, registry=None)
        assert out.get("success") is False
        assert "registry" in str(out.get("error", ""))
        assert reg_with_sandbox._demo_executed == [], "缺 registry 时 handler 不应执行"
