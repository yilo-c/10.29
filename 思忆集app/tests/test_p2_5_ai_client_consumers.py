"""AIClientBinding + apply_ai_client_bindings 单元测试（P2-5 批次 B）。

覆盖：
- AIClientConsumer Protocol runtime_checkable
- AIClientBinding.apply:
    * method 优先于 fields
    * fields 按顺序回退
    * require_not_none 控制
    * attr 缺失 / target 为 None / 没有匹配字段 返回 False
- apply_ai_client_bindings:
    * 6 个默认 binding 在 mock ctx 上的注入结果
    * 单个 binding 抛异常不影响其他
- DEFAULT_AI_CLIENT_BINDINGS 与 recreate_ai_client 原有 6 消费者一致
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app_runtime.ai_client_consumers import (
    DEFAULT_AI_CLIENT_BINDINGS,
    AIClientBinding,
    AIClientConsumer,
    apply_ai_client_bindings,
)


# ═══════════════════════════════════════════════════════════════
# Protocol runtime check
# ═══════════════════════════════════════════════════════════════


class TestAIClientConsumerProtocol:
    def test_object_with_update_ai_client_satisfies_protocol(self):
        class Consumer:
            def update_ai_client(self, new_client):
                self.client = new_client

        assert isinstance(Consumer(), AIClientConsumer)

    def test_object_without_method_does_not_satisfy(self):
        assert not isinstance(SimpleNamespace(), AIClientConsumer)


# ═══════════════════════════════════════════════════════════════
# AIClientBinding.apply
# ═══════════════════════════════════════════════════════════════


class TestBindingApply:
    def test_attr_missing_returns_false(self):
        ctx = SimpleNamespace()
        b = AIClientBinding(attr="missing")
        assert b.apply(ctx, "client") is False

    def test_target_none_returns_false(self):
        ctx = SimpleNamespace(intent_router=None)
        b = AIClientBinding(attr="intent_router")
        assert b.apply(ctx, "client") is False

    def test_method_takes_priority(self):
        captured = {}

        def setter(c):
            captured["via"] = "method"
            captured["client"] = c

        target = MagicMock()
        target.set_ai_client = setter
        # field 也存在但不应被使用
        target.ai_client = "old"
        ctx = SimpleNamespace(intent_router=target)
        b = AIClientBinding(attr="intent_router", method="set_ai_client", fields=("ai_client",))
        assert b.apply(ctx, "new") is True
        assert captured == {"via": "method", "client": "new"}
        assert target.ai_client == "old"  # 字段未被覆盖

    def test_fields_fallback_in_order(self):
        # _ai_client 存在 → 用它；不动 ai_client
        target = SimpleNamespace(_ai_client=None, ai_client="old")
        ctx = SimpleNamespace(copilot_engine=target)
        b = AIClientBinding(attr="copilot_engine", fields=("_ai_client", "ai_client"))
        assert b.apply(ctx, "new") is True
        assert target._ai_client == "new"
        assert target.ai_client == "old"

    def test_fields_second_used_when_first_missing(self):
        # 用 type-stub: 没有 _ai_client，应回退到 ai_client
        class Target:
            ai_client = "old"
        t = Target()
        ctx = SimpleNamespace(copilot_engine=t)
        b = AIClientBinding(attr="copilot_engine", fields=("_ai_client", "ai_client"))
        assert b.apply(ctx, "new") is True
        assert t.ai_client == "new"
        assert not hasattr(t, "_ai_client")

    def test_require_not_none_skips_when_none(self):
        target = SimpleNamespace(_ai_client=None)
        ctx = SimpleNamespace(suggestion_engine=target)
        b = AIClientBinding(
            attr="suggestion_engine",
            fields=("_ai_client",),
            require_not_none=True,
        )
        assert b.apply(ctx, "new") is False
        assert target._ai_client is None  # 未覆盖

    def test_require_not_none_updates_when_set(self):
        target = SimpleNamespace(_ai_client="existing")
        ctx = SimpleNamespace(suggestion_engine=target)
        b = AIClientBinding(
            attr="suggestion_engine",
            fields=("_ai_client",),
            require_not_none=True,
        )
        assert b.apply(ctx, "new") is True
        assert target._ai_client == "new"

    def test_no_matching_field_returns_false(self):
        target = SimpleNamespace(unrelated="x")
        ctx = SimpleNamespace(target=target)
        b = AIClientBinding(attr="target", fields=("ai_client",))
        assert b.apply(ctx, "new") is False


# ═══════════════════════════════════════════════════════════════
# apply_ai_client_bindings
# ═══════════════════════════════════════════════════════════════


class TestApplyBindings:
    def _make_full_ctx(self):
        """构造 6 个消费者齐全的 mock context（复刻 recreate_ai_client 原有覆盖范围）。"""
        intent_router = MagicMock()
        intent_router.set_ai_client = MagicMock()
        browser_bridge = SimpleNamespace(ai_service=None)
        aam = SimpleNamespace(ai_client=None)
        copilot = SimpleNamespace(_ai_client=None, ai_client="legacy")
        suggestion = SimpleNamespace(_ai_client="existing")
        inbox = SimpleNamespace(ai_client=None)

        ctx = SimpleNamespace(
            intent_router=intent_router,
            browser_assistant_bridge=browser_bridge,
            autonomous_assistant_manager=aam,
            copilot_engine=copilot,
            suggestion_engine=suggestion,
            inbox_capture_service=inbox,
        )
        return ctx, {
            "intent_router": intent_router,
            "browser_assistant_bridge": browser_bridge,
            "autonomous_assistant_manager": aam,
            "copilot_engine": copilot,
            "suggestion_engine": suggestion,
            "inbox_capture_service": inbox,
        }

    def test_full_injection(self):
        ctx, parts = self._make_full_ctx()
        applied = apply_ai_client_bindings(ctx, "new_client")
        assert applied == [
            "intent_router",
            "browser_assistant_bridge",
            "autonomous_assistant_manager",
            "copilot_engine",
            "suggestion_engine",
            "inbox_capture_service",
        ]
        parts["intent_router"].set_ai_client.assert_called_once_with("new_client")
        assert parts["browser_assistant_bridge"].ai_service == "new_client"
        assert parts["autonomous_assistant_manager"].ai_client == "new_client"
        # copilot：优先 _ai_client，legacy 字段不应被覆盖
        assert parts["copilot_engine"]._ai_client == "new_client"
        assert parts["copilot_engine"].ai_client == "legacy"
        assert parts["suggestion_engine"]._ai_client == "new_client"
        assert parts["inbox_capture_service"].ai_client == "new_client"

    def test_partial_ctx_missing_attrs_silently_skipped(self):
        ctx = SimpleNamespace(
            intent_router=MagicMock(set_ai_client=MagicMock()),
            inbox_capture_service=SimpleNamespace(ai_client=None),
        )
        applied = apply_ai_client_bindings(ctx, "c")
        assert applied == ["intent_router", "inbox_capture_service"]

    def test_suggestion_engine_skipped_when_ai_client_none(self):
        ctx = SimpleNamespace(
            suggestion_engine=SimpleNamespace(_ai_client=None),
        )
        applied = apply_ai_client_bindings(ctx, "c")
        assert applied == []

    def test_intent_router_fallback_to_field(self):
        # 没有 set_ai_client，回退到 _ai_client
        ctx = SimpleNamespace(intent_router=SimpleNamespace(_ai_client=None))
        applied = apply_ai_client_bindings(ctx, "c")
        assert applied == ["intent_router"]
        assert ctx.intent_router._ai_client == "c"

    def test_exception_isolated(self):
        class Boom:
            @property
            def ai_client(self):
                return None
            @ai_client.setter
            def ai_client(self, v):
                raise RuntimeError("boom")

        ctx = SimpleNamespace(
            autonomous_assistant_manager=Boom(),
            inbox_capture_service=SimpleNamespace(ai_client=None),
        )
        applied = apply_ai_client_bindings(ctx, "c")
        # AAM 抛异常不影响 inbox
        assert "inbox_capture_service" in applied
        assert "autonomous_assistant_manager" not in applied


class TestDefaultBindings:
    def test_default_bindings_cover_original_six_consumers(self):
        attrs = {b.attr for b in DEFAULT_AI_CLIENT_BINDINGS}
        assert attrs == {
            "intent_router",
            "browser_assistant_bridge",
            "autonomous_assistant_manager",
            "copilot_engine",
            "suggestion_engine",
            "inbox_capture_service",
            "job_hunter_service",
        }

    def test_intent_router_uses_set_ai_client_method(self):
        b = next(b for b in DEFAULT_AI_CLIENT_BINDINGS if b.attr == "intent_router")
        assert b.method == "set_ai_client"

    def test_browser_bridge_uses_ai_service_field(self):
        b = next(b for b in DEFAULT_AI_CLIENT_BINDINGS if b.attr == "browser_assistant_bridge")
        assert b.fields == ("ai_service",)

    def test_suggestion_engine_require_not_none(self):
        b = next(b for b in DEFAULT_AI_CLIENT_BINDINGS if b.attr == "suggestion_engine")
        assert b.require_not_none is True
        assert b.fields == ("_ai_client",)
