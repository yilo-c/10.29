# -*- coding: utf-8 -*-
"""P1-4：领域子门面测试（不实例化 AppContext，纯代理逻辑 + 契约）。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app_runtime.context_facades import (
    AutomationFacade,
    ChatFacade,
    MemoryFacade,
    SyncFacade,
)


def test_facade_proxies_whitelisted_attrs():
    ctx = SimpleNamespace(ai_client="AI", trace_recorder="REC", cron_service="CRON")
    assert ChatFacade(ctx).ai_client == "AI"
    assert ChatFacade(ctx).trace_recorder == "REC"
    assert AutomationFacade(ctx).cron_service == "CRON"


def test_facade_missing_attr_returns_none():
    ctx = SimpleNamespace()
    assert ChatFacade(ctx).ai_client is None
    assert SyncFacade(ctx).cloud_sync_bridge is None


def test_facade_non_whitelisted_attr_raises():
    ctx = SimpleNamespace(dangerous="x")
    with pytest.raises(AttributeError):
        _ = ChatFacade(ctx).dangerous


def test_all_facades_declare_attrs():
    for cls in (ChatFacade, MemoryFacade, SyncFacade, AutomationFacade):
        assert cls._attrs, f"{cls.__name__} 应声明白名单属性"


def test_app_context_exposes_facade_properties():
    """契约：AppContext 提供 chat/memory/sync/automation 四个门面属性。"""
    from app_runtime.app_context import AppContext

    for name in ("chat", "memory", "sync", "automation"):
        prop = getattr(AppContext, name, None)
        assert isinstance(prop, property), f"AppContext.{name} 应为 property"


@pytest.mark.asyncio
async def test_trace_handlers_work_via_facade_and_plain_context(tmp_path):
    """_trace handler 同时兼容门面 context 与裸属性 context。"""
    from services.trace_recorder import TraceRecorder
    from backend.handlers._trace import register_trace

    class FakeRegistry:
        def __init__(self):
            self.handlers = {}

        def register(self, name, fn):
            self.handlers[name] = fn

    rec = TraceRecorder(str(tmp_path / "t.db"))
    try:
        rec.start_run("x", "cron")
        rec.finish_run("x", "completed")

        # 裸属性 context（旧风格）
        ctx_plain = SimpleNamespace(trace_recorder=rec, autonomous_assistant_manager=None)
        reg1 = FakeRegistry()
        register_trace(reg1, ctx_plain)
        r1 = await reg1.handlers["trace.list"]()
        assert r1["success"] and r1["count"] == 1

        # 门面 context（新风格：AppContext 有 chat/automation 属性）
        class FacadeCtx:
            trace_recorder = rec
            autonomous_assistant_manager = None

            @property
            def chat(self):
                return ChatFacade(self)

            @property
            def automation(self):
                return AutomationFacade(self)

        reg2 = FakeRegistry()
        register_trace(reg2, FacadeCtx())
        r2 = await reg2.handlers["trace.list"]()
        assert r2["success"] and r2["count"] == 1
    finally:
        rec.close()
