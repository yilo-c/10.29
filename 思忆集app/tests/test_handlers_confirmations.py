"""confirmation.* handlers 行为测试（T6.1）。

覆盖：confirmation.list / confirmation.status（含 not_found）/ confirmation.resolve
（批准理由必填校验 + 委托 butler resolve）/ 注册齐全。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.handlers._confirmations import (
    handle_confirmation_list,
    handle_confirmation_status,
    handle_confirmation_resolve,
    register_confirmations,
)


class _Registry:
    def __init__(self):
        self.registered: dict[str, object] = {}

    def register(self, name: str, fn) -> None:
        self.registered[name] = fn

    def register_with_schema(self, name: str, fn, schema=None) -> None:
        # P2 修复后 status/resolve 走 schema 注册，fake 与生产 MethodRegistry 对齐
        self.registered[name] = fn


# ── confirmation.list ──


def test_list_returns_store_contents(tmp_path: Path) -> None:
    store_data = [
        {"confirmation_id": "c1", "action_id": "note.create", "status": "pending"},
        {"confirmation_id": "c2", "action_id": "task.create", "status": "pending"},
    ]
    fake_store = SimpleNamespace(list=lambda: store_data)
    with patch("backend.handlers._confirmations.get_confirmation_store", return_value=fake_store):
        result = asyncio.run(handle_confirmation_list({}))
    # 2026-10-15 帧守卫批次：响应恒带 truncated 标记（未截断 = False）
    assert result == {"confirmations": store_data, "truncated": False}


def test_list_empty(tmp_path: Path) -> None:
    fake_store = SimpleNamespace(list=lambda: [])
    with patch("backend.handlers._confirmations.get_confirmation_store", return_value=fake_store):
        result = asyncio.run(handle_confirmation_list({}))
    assert result == {"confirmations": [], "truncated": False}


# ── confirmation.status ──


def test_status_found(tmp_path: Path) -> None:
    entry = {
        "confirmation_id": "c1",
        "action_id": "note.create",
        "status": "pending",
        "source": "butler",
        "reason": "",
        "created_at": "2026-08-23T00:00:00+00:00",
        "expires_at": "2026-08-24T00:00:00+00:00",
        "result": None,
    }
    fake_store = SimpleNamespace(get=lambda cid: entry if cid == "c1" else None)
    with patch("backend.handlers._confirmations.get_confirmation_store", return_value=fake_store):
        result = asyncio.run(handle_confirmation_status({"confirmation_id": "c1"}))
    assert result["confirmation_id"] == "c1"
    assert result["status"] == "pending"
    assert result["action_id"] == "note.create"


def test_status_not_found(tmp_path: Path) -> None:
    fake_store = SimpleNamespace(get=lambda cid: None)
    with patch("backend.handlers._confirmations.get_confirmation_store", return_value=fake_store):
        result = asyncio.run(handle_confirmation_status({"confirmation_id": "missing"}))
    assert result["status"] == "expired_or_not_found"
    assert result["confirmation_id"] == "missing"


# ── confirmation.resolve ──


def test_resolve_approved_without_reason_rejected(tmp_path: Path) -> None:
    """批准操作必须填理由（H 项后端校验），不带理由被拦。"""
    with patch("backend.handlers._confirmations.get_confirmation_store") as _:
        result = asyncio.run(handle_confirmation_resolve({"confirmation_id": "c1", "approved": True}))
    assert result["success"] is False
    assert result["approved"] is True
    assert result["reason"] == "approval_reason_required"


def test_resolve_approved_with_reason_delegates(tmp_path: Path) -> None:
    """批准 + 理由 -> 委托稳定层 services.confirmation_flow.resolve_confirmation。"""

    async def _fake_resolve(cid, approved, reason=""):
        return {"success": True, "action_id": "note.create", "approved": approved, "reason": reason}

    with patch("services.confirmation_flow.resolve_confirmation", side_effect=_fake_resolve) as mock_resolve:
        result = asyncio.run(handle_confirmation_resolve({"confirmation_id": "c1", "approved": True, "reason": "用户确认"}))
    assert result["success"] is True
    mock_resolve.assert_awaited_once_with("c1", True, reason="用户确认")


def test_resolve_reject_without_reason_delegates(tmp_path: Path) -> None:
    """拒绝操作不强制理由（缺省空串，向后兼容）。"""

    async def _fake_resolve(cid, approved, reason=""):
        return {"success": True, "approved": approved, "reason": reason}

    with patch("services.confirmation_flow.resolve_confirmation", side_effect=_fake_resolve) as mock_resolve:
        result = asyncio.run(handle_confirmation_resolve({"confirmation_id": "c1", "approved": False}))
    assert result["success"] is True
    mock_resolve.assert_awaited_once_with("c1", False, reason="")


# ── 注册 ──


def test_register_all_confirmation_methods() -> None:
    reg = _Registry()
    register_confirmations(reg, None)
    assert "confirmation.list" in reg.registered
    assert "confirmation.status" in reg.registered
    assert "confirmation.resolve" in reg.registered


# ── P2 回归（rpc-census-2026-08-30）：空参调用返回干净参数错误 ──
# 修复前：handlers 签名要求必填位置参数 params，registry.dispatch 以
# handler(**params) 展开调用，空参直接 TypeError → -32603 broken。


def test_dispatch_empty_params_status_raises_invalid_params() -> None:
    """confirmation.status 空参 → -32602 InvalidParamsError（不再 TypeError）。"""
    from backend.method_registry import MethodRegistry
    from core.errors import InvalidParamsError

    reg = MethodRegistry()
    register_confirmations(reg, None)
    with pytest.raises(InvalidParamsError) as ei:
        asyncio.run(reg.dispatch("confirmation.status", {}))
    assert ei.value.code == -32602


def test_dispatch_empty_params_resolve_raises_invalid_params() -> None:
    """confirmation.resolve 空参 → -32602 InvalidParamsError。"""
    from backend.method_registry import MethodRegistry
    from core.errors import InvalidParamsError

    reg = MethodRegistry()
    register_confirmations(reg, None)
    with pytest.raises(InvalidParamsError) as ei:
        asyncio.run(reg.dispatch("confirmation.resolve", {}))
    assert ei.value.code == -32602


def test_dispatch_empty_params_list_ok() -> None:
    """confirmation.list 无必填参数：空参应正常返回而非 TypeError。"""
    from backend.method_registry import MethodRegistry

    reg = MethodRegistry()
    register_confirmations(reg, None)
    fake_store = SimpleNamespace(list=lambda: [])
    with patch("backend.handlers._confirmations.get_confirmation_store", return_value=fake_store):
        result = asyncio.run(reg.dispatch("confirmation.list", {}))
    # 2026-10-15 帧守卫批次：响应恒带 truncated 标记
    assert result == {"confirmations": [], "truncated": False}


def test_dispatch_status_with_id_still_works() -> None:
    """带 confirmation_id 的调用不受 schema 影响（schema 只拦缺失键）。"""
    from backend.method_registry import MethodRegistry

    reg = MethodRegistry()
    register_confirmations(reg, None)
    fake_store = SimpleNamespace(get=lambda cid: None)
    with patch("backend.handlers._confirmations.get_confirmation_store", return_value=fake_store):
        result = asyncio.run(reg.dispatch("confirmation.status", {"confirmation_id": "ghost"}))
    assert result["status"] == "expired_or_not_found"
