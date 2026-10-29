"""personal.* 空参回归测试（P2 归因，rpc-census-2026-08-30）。

普查中 personal.profile.extract 的 "ws closed: 8" 是普查客户端限速
（180/60s）超过服务端单连接消息限流（SIYI_WS_MSG_RATE_MAX 默认 120/60s）
导致的服务端主动断连——close 发生在该消息被分发之前，方法本身未被调用，
handler 无缺陷。此处锁死其空参调用的健康行为：不抛异常、返回结构化 dict。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


from backend.method_registry import MethodRegistry


def _make_registry() -> MethodRegistry:
    from backend.handlers._personal import register_personal

    reg = MethodRegistry()
    # context 不带 raw_item_repo / user_profile_service → 走降级路径
    register_personal(reg, SimpleNamespace())
    return reg


def test_profile_extract_empty_params_clean() -> None:
    """空参 dispatch → 结构化降级 dict，不抛任何异常。"""
    reg = _make_registry()
    result = asyncio.run(reg.dispatch("personal.profile.extract", {}))
    assert result["success"] is False
    assert "error" in result


def test_profile_get_empty_params_clean() -> None:
    """personal.profile.get 空参 → 结构化降级 dict。"""
    reg = _make_registry()
    result = asyncio.run(reg.dispatch("personal.profile.get", {}))
    assert result["success"] is False


def test_raw_items_set_sensitivity_empty_params_clean() -> None:
    """personal.raw_items.set_sensitivity 空参 → 结构化参数校验拒绝。"""
    reg = _make_registry()
    result = asyncio.run(reg.dispatch("personal.raw_items.set_sensitivity", {}))
    # repo 未初始化时先走降级分支；断言点为「结构化 dict、不抛异常」
    assert result["success"] is False
    assert isinstance(result["error"], str) and result["error"]


def test_profile_extract_with_services_and_empty_items() -> None:
    """repo/svc 就绪但无未处理条目 → 空参调用返回成功统计（不崩连接）。"""
    from backend.handlers._personal import register_personal

    class _Repo:
        def list(self, processed=0, limit=200, **kwargs):
            return []

        def mark_processed_many(self, ids):
            return 0

    class _Svc:
        def load(self):
            return {}

        def merge_key_people(self, people):
            pass

        def merge_focus_topics(self, topics):
            pass

        def set_current_focus(self, focus):
            pass

    reg = MethodRegistry()
    ctx = SimpleNamespace(raw_item_repo=_Repo(), user_profile_service=_Svc(), ai_client=None)
    register_personal(reg, ctx)
    result = asyncio.run(reg.dispatch("personal.profile.extract", {}))
    assert result["success"] is True
    assert result["scanned"] == 0
