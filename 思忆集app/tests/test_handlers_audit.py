"""audit.get / audit.can_rollback 空参回归测试（P2 修复，rpc-census-2026-08-30）。

修复前：签名要求必填位置参数 entry_id，registry.dispatch 以 handler(**params)
展开调用，空参（params={}）直接 TypeError → -32603 broken。
修复后：签名默认值 + 注册层 JSON Schema required=entry_id → 空参返回干净
-32602 InvalidParamsError；entry_id="" 等既有降级 dict 行为保持不变。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from backend.method_registry import MethodRegistry
from core.errors import InvalidParamsError


def _make_registry() -> MethodRegistry:
    from backend.handlers._audit import register_audit

    reg = MethodRegistry()
    # context 不带 audit_trail / rollback_manager → 走降级路径
    register_audit(reg, SimpleNamespace())
    return reg


@pytest.mark.parametrize("method", ["audit.get", "audit.can_rollback"])
def test_empty_params_returns_invalid_params(method: str) -> None:
    """空参 dispatch → -32602（修复前为 TypeError/-32603 broken）。"""
    reg = _make_registry()
    with pytest.raises(InvalidParamsError) as ei:
        asyncio.run(reg.dispatch(method, {}))
    assert ei.value.code == -32602


def test_get_empty_entry_id_keeps_legacy_degrade() -> None:
    """entry_id=""（键存在但为空）保持既有降级 dict，不被 schema 误伤。"""
    reg = _make_registry()
    result = asyncio.run(reg.dispatch("audit.get", {"entry_id": ""}))
    assert result == {"error": "audit trail not initialized"}


def test_list_empty_params_ok() -> None:
    """audit.list 无必填参数：空参正常返回降级 dict。"""
    reg = _make_registry()
    result = asyncio.run(reg.dispatch("audit.list", {}))
    assert result == {"error": "audit trail not initialized", "entries": []}


def test_stats_empty_params_ok() -> None:
    """audit.stats 无必填参数：空参正常返回降级 dict。"""
    reg = _make_registry()
    result = asyncio.run(reg.dispatch("audit.stats", {}))
    assert result == {"error": "audit trail not initialized"}
