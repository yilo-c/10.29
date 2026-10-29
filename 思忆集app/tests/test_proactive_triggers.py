"""ProactiveTriggerEngine 模块迁移后冒烟测试（S1.1 §5.1.1）。

覆盖：模块已从 assistant/ 迁至 services/ 且可被导入；核心类与关键方法存在；
构造器与 tick() 在给定 context 下不抛异常（不依赖 assistant 内核）。
"""

from __future__ import annotations

import inspect

from services.proactive_triggers import ProactiveTriggerEngine


def test_module_lives_in_services_zone():
    # S1.1 Zone 纪律：模块必须位于 services 稳定区，而非 assistant 内核
    assert ProactiveTriggerEngine.__module__ == "services.proactive_triggers"


def test_engine_is_constructible():
    # 构造器接受可选依赖注入（迁移后保持原签名，不依赖 assistant 内核）
    engine = ProactiveTriggerEngine(inbox_service=object(), event_bus=None)
    assert engine is not None
    assert engine._inbox is not None
    # 不传依赖时所有可选依赖默认为 None，构造不抛异常
    engine2 = ProactiveTriggerEngine()
    assert engine2._inbox is None


def test_key_dispatch_methods_exist():
    # tick / deliver / deliver_all 是 cron action proactive.tick 的入口，必须存在且可调用
    for name in ("tick", "deliver", "deliver_all", "notify_interaction",
                 "notify_emotion_change", "notify_content_created",
                 "notify_selection", "notify_blank_hotkey", "notify_url_intercept"):
        assert hasattr(ProactiveTriggerEngine, name), f"缺少方法 {name}"
        assert callable(getattr(ProactiveTriggerEngine, name))


def test_tick_returns_list_without_throwing():
    engine = ProactiveTriggerEngine()
    # 默认所有 trigger 启用；tick 在无交互上下文下应返回（可能为空的）list，不抛异常
    result = engine.tick(context={"idle_minutes": 0})
    assert isinstance(result, list)


def test_set_enabled_trigger_filters_idle():
    engine = ProactiveTriggerEngine()
    engine.set_enabled_triggers({"idle"})
    assert engine.is_trigger_enabled("idle") is True
    assert engine.is_trigger_enabled("morning_brief") is False
