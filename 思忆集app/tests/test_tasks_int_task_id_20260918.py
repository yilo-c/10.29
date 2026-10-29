"""`tasks.schedule` / `tasks.unschedule` 的 int task_id 回归（风险台账 #181，2026-09-18）。

原实现 ``task_id = (task_id or "").strip()`` —— **假设入参一定是 str**。
JSON-RPC 不做类型校验，客户端传 ``{"task_id": 5}`` 时直接
``AttributeError: 'int' object has no attribute 'strip'`` → -32603。

同一文件的 ``_find_task_row`` / ``_resolve_task_id``（:15/:48）早已接受
``int | str``，说明"task_id 可能是 int"在本模块是**已知形态**，只是这两个
注册点漏了归一。

★ 空参普查（rpc_census）**抓不到**这条：默认 ``""`` 不会触发 strip 崩溃。
这正是"L1 空参普查 ≠ 功能正确"的实证，故单独补带参回归。
"""

from __future__ import annotations

import types

import pytest

from backend.handlers._tasks import register_tasks
from backend.method_registry import MethodRegistry


@pytest.fixture()
def registry() -> MethodRegistry:
    """只注册 tasks 域；context 故意**不带** task_manager。

    这样 int 归一走到 ``task_id`` 校验之后就会命中
    ``{"success": False, "error": "task manager not available"}`` ——
    既能证明 strip 没崩，又不需要真实仓储/定时服务。
    """
    reg = MethodRegistry()
    register_tasks(reg, types.SimpleNamespace())
    return reg


@pytest.mark.asyncio
async def test_schedule_accepts_int_task_id(registry: MethodRegistry) -> None:
    """int task_id 不得抛 AttributeError（旧实现 -32603）。"""
    result = await registry.dispatch("tasks.schedule", {"task_id": 5})
    assert isinstance(result, dict), f"期望结构化 dict，实际 {result!r}"
    assert result.get("error") == "task manager not available"


@pytest.mark.asyncio
async def test_unschedule_accepts_int_task_id(registry: MethodRegistry) -> None:
    result = await registry.dispatch("tasks.unschedule", {"task_id": 5})
    assert isinstance(result, dict), f"期望结构化 dict，实际 {result!r}"
    assert result.get("error") == "task manager scheduling unavailable"


@pytest.mark.asyncio
async def test_schedule_still_rejects_empty_task_id(registry: MethodRegistry) -> None:
    """归一不得破坏既有语义：空 task_id 仍是 'task_id required'。"""
    for empty in ("", None):
        result = await registry.dispatch("tasks.schedule", {"task_id": empty})
        assert result == {"success": False, "error": "task_id required"}


@pytest.mark.asyncio
async def test_schedule_string_task_id_unchanged(registry: MethodRegistry) -> None:
    """字符串 task_id 行为不变（含去空白）。"""
    result = await registry.dispatch("tasks.schedule", {"task_id": "  abc  "})
    assert result.get("error") == "task manager not available"
