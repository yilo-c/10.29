from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app_runtime.memory_manager import MemoryManager


def test_update_ai_client_refreshes_nested_extractors():
    manager = MemoryManager.__new__(MemoryManager)
    old_client = object()
    new_client = object()

    manager._memory = SimpleNamespace(ai_client=old_client)
    manager.knowledge_graph_extractor = SimpleNamespace(ai_client=old_client)
    manager.memory_extractor = SimpleNamespace(ai_client=old_client)

    manager.update_ai_client(new_client)

    assert manager._memory.ai_client is new_client
    assert manager.knowledge_graph_extractor.ai_client is new_client
    assert manager.memory_extractor.ai_client is new_client


# ── P1-17 透传瘦身回归 ──

def _make_bare_manager(memory_stub):
    """构造一个跳过 __init__ 的 MemoryManager，仅注入 _memory，用于 __getattr__ 测试。"""
    manager = MemoryManager.__new__(MemoryManager)
    manager._memory = memory_stub
    return manager


def test_getattr_forwards_method_call_to_memory_context():
    """未在 MemoryManager 显式定义的方法应转发到 self._memory。"""
    calls = []

    def fake_get_due(limit=20):
        calls.append(("get_due_memories", limit))
        return [{"id": "m1"}]

    manager = _make_bare_manager(SimpleNamespace(get_due_memories=fake_get_due))
    result = manager.get_due_memories(limit=5)

    assert result == [{"id": "m1"}]
    assert calls == [("get_due_memories", 5)]


def test_getattr_forwards_link_memory_with_args():
    """link_memory 也应自动转发，并保留位置/关键字参数。"""
    received = {}

    def fake_link_memory(entry, existing=None):
        received["entry"] = entry
        received["existing"] = existing
        return ["linked"]

    manager = _make_bare_manager(SimpleNamespace(link_memory=fake_link_memory))
    out = manager.link_memory({"id": "x"}, existing=[{"id": "y"}])

    assert out == ["linked"]
    assert received == {"entry": {"id": "x"}, "existing": [{"id": "y"}]}


def test_getattr_raises_for_unknown_attribute():
    """_memory 上不存在的属性应抛 AttributeError，避免静默吞错。"""
    manager = _make_bare_manager(SimpleNamespace())
    try:
        manager.no_such_method
    except AttributeError:
        return
    raise AssertionError("expected AttributeError for unknown attribute")


def test_getattr_refuses_to_forward_dunder_or_private():
    """私有/dunder 属性不应被转发，否则会与 Python 协议冲突。"""
    manager = _make_bare_manager(SimpleNamespace(_secret="should-not-be-exposed"))
    try:
        manager._secret
    except AttributeError:
        return
    raise AssertionError("expected AttributeError for private attribute")


def test_getattr_safe_before_memory_initialized():
    """初始化期（_memory 尚未就绪）不能因 __getattr__ 进入无限递归。"""
    manager = MemoryManager.__new__(MemoryManager)
    # 不设置 _memory
    try:
        manager.get_due_memories
    except AttributeError:
        return
    raise AssertionError("expected AttributeError when _memory missing")


def test_explicit_supertag_methods_still_use_tag_service():
    """5 个 supertag 方法仍显式走 tag_service（而非走 __getattr__）。"""

    class FakeSchemaRegistry:
        def list_schemas(self):
            return {"book": {}}

        def get_schema(self, t):
            return {"type": t}

        def register_schema(self, s):
            return {"ok": True, "schema": s}

    class FakeTagService:
        def __init__(self):
            self.schema_registry = FakeSchemaRegistry()
            self.set_calls = []
            self.get_calls = []

        def set_structured_data(self, entry_id, supertag_type, field_data):
            self.set_calls.append((entry_id, supertag_type, field_data))
            return {"ok": True}

        def get_structured_data(self, entry_id):
            self.get_calls.append(entry_id)
            return {"entry_id": entry_id}

    manager = _make_bare_manager(SimpleNamespace())
    manager.tag_service = FakeTagService()

    assert manager.get_supertag_schemas() == {"book": {}}
    assert manager.get_supertag_schema("book") == {"type": "book"}
    assert manager.register_supertag_schema({"name": "x"}) == {"ok": True, "schema": {"name": "x"}}
    assert manager.set_entry_supertag_data("e1", "book", {"author": "z"}) == {"ok": True}
    assert manager.tag_service.set_calls == [("e1", "book", {"author": "z"})]
    assert manager.get_entry_supertag_data("e2") == {"entry_id": "e2"}
    assert manager.tag_service.get_calls == ["e2"]
