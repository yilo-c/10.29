"""info.items.* 空参回归测试（P2 修复，rpc-census-2026-08-30）。

修复前：info.items.get / toggle_read / toggle_saved / update / save_to_note
签名要求必填位置参数 item_id，registry.dispatch 以 handler(**params) 展开
调用，空参（params={}）直接 TypeError → -32603 broken。
修复后：签名默认值（直调兜底走既有 invalid item_id 降级）+ 注册层 JSON
Schema required=item_id → 空参返回干净 -32602 InvalidParamsError。

info.items.sources / stats 无必填参数：普查中的 "ws closed: 8" 是普查客户端
限速（180/60s）超过服务端单连接消息限流（SIYI_WS_MSG_RATE_MAX 默认 120/60s）
导致的服务端主动断连，方法本身未被调用；此处锁死其空参健康行为。
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
    from backend.handlers import _info_items

    reg = MethodRegistry()
    ctx = SimpleNamespace(info_item_repository=None)
    asyncio.run(_info_items.register(reg, ctx))
    return reg


@pytest.mark.parametrize(
    "method",
    [
        "info.items.get",
        "info.items.toggle_read",
        "info.items.toggle_saved",
        "info.items.update",
        "info.items.save_to_note",
    ],
)
def test_empty_params_returns_invalid_params(method: str) -> None:
    """空参 dispatch → -32602（修复前为 TypeError/-32603 broken）。"""
    reg = _make_registry()
    with pytest.raises(InvalidParamsError) as ei:
        asyncio.run(reg.dispatch(method, {}))
    assert ei.value.code == -32602


def test_sources_empty_params_ok() -> None:
    """sources 无必填参数：空参正常返回（普查误伤方法的行为锁死）。"""
    reg = _make_registry()
    result = asyncio.run(reg.dispatch("info.items.sources", {}))
    assert result == {"sources": []}


def test_stats_empty_params_ok() -> None:
    """stats 无必填参数：空参正常返回。"""
    reg = _make_registry()
    result = asyncio.run(reg.dispatch("info.items.stats", {}))
    assert result == {"stats": {}}


def test_get_with_item_id_but_no_repo_degrades() -> None:
    """repo 缺失时保持既有降级 dict（不抛异常、不受 schema 影响）。"""
    reg = _make_registry()
    result = asyncio.run(reg.dispatch("info.items.get", {"item_id": "x"}))
    assert result == {"item": None, "error": "invalid item_id"}


def test_direct_call_without_args_degrades() -> None:
    """绕过 registry 的直调（无参）→ 走签名默认值兜底，不抛 TypeError。"""
    from backend.handlers import _info_items

    reg = MethodRegistry()
    ctx = SimpleNamespace(info_item_repository=None)
    asyncio.run(_info_items.register(reg, ctx))
    # 从注册表取 handler 直调（模拟内部调用路径）
    handler = reg._handlers["info.items.get"]
    result = asyncio.run(handler())
    assert result == {"item": None, "error": "invalid item_id"}
