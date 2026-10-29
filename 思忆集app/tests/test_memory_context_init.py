"""P2-26 回归：MemoryContext.__init__ 按域拆分后的契约测试。

只验证：
1. 5 个私有 _init_<domain> 助手存在且为方法；
2. __init__ 完整跑通后，9 个对外服务属性均被赋值；
3. 3 个 supertag.* 事件订阅已注册到 EventBus；
4. 公共 API 数量保持稳定（防止误删方法）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from memory.context import MemoryContext


# ── helpers ──────────────────────────────────────────────────────────


def _build_ctx(tmp_path: Path) -> MemoryContext:
    data_store = MagicMock(name="data_store")
    ai_client = MagicMock(name="ai_client")
    settings_repository = MagicMock(name="settings_repository")
    user_profile_service = MagicMock(name="user_profile_service")
    note_repository = MagicMock(name="note_repository")
    task_repository = MagicMock(name="task_repository")
    inbox_repository = MagicMock(name="inbox_repository")

    return MemoryContext(
        data_store=data_store,
        data_base_dir=str(tmp_path),
        ai_client=ai_client,
        settings_repository=settings_repository,
        user_profile_service=user_profile_service,
        note_repository=note_repository,
        task_repository=task_repository,
        inbox_repository=inbox_repository,
        hybrid_search_service=None,
    )


# ── 1. 私有助手存在 ─────────────────────────────────────────────────


def test_all_five_init_helpers_are_methods():
    helpers = [
        "_init_hybrid_search",
        "_init_index_and_tags",
        "_init_knowledge_graph",
        "_init_memory_services",
        "_init_structured_store",
    ]
    for name in helpers:
        attr = getattr(MemoryContext, name, None)
        assert attr is not None, f"missing helper {name}"
        assert callable(attr), f"{name} is not callable"


# ── 2. 构造完成后 9 个服务属性都已赋值 ─────────────────────────────


def test_constructor_sets_all_service_attributes(tmp_path):
    ctx = _build_ctx(tmp_path)

    expected_attrs = [
        "_hybrid_memory_search",
        "memory_index_service",
        "tag_service",
        "knowledge_graph_store",
        "knowledge_graph_extractor",
        "memory_extractor",
        "memory_scheduler",
        "memory_linker",
        "structured_memory_store",
    ]
    for attr in expected_attrs:
        assert hasattr(ctx, attr), f"missing attribute {attr}"
        assert getattr(ctx, attr) is not None, f"{attr} should not be None"


# ── 3. EventBus 订阅已注册 ─────────────────────────────────────────


def test_supertag_event_subscriptions_registered(tmp_path):
    from core.events import event_bus

    # 抓快照避免被其他测试污染
    snapshot_before = {
        evt: list(event_bus._handlers.get(evt, []))
        for evt in ("supertag.created", "supertag.updated", "supertag.deleted")
    }

    ctx = _build_ctx(tmp_path)

    for evt in ("supertag.created", "supertag.updated", "supertag.deleted"):
        handlers = event_bus._handlers.get(evt, [])
        # 至少多了一个新订阅
        assert len(handlers) > len(snapshot_before[evt]), (
            f"event {evt} did not register a new handler"
        )
        # 新增的那个应该是 ctx._on_supertag_event
        assert ctx._on_supertag_event in handlers

    # 清理：把这次注册的 handler 摘掉，避免污染其他测试
    for evt in ("supertag.created", "supertag.updated", "supertag.deleted"):
        event_bus.off(evt, ctx._on_supertag_event)


# ── 4. 公共 API 数量稳定（防止重构误删方法） ──────────────────────


def test_public_method_count_stable(tmp_path):
    ctx = _build_ctx(tmp_path)
    public = [m for m in dir(ctx) if not m.startswith("_")]
    # P2-26 拆分前为 32 个公共属性/方法；允许 ±2 容差，但不允许大幅缩减
    assert len(public) >= 30, f"public surface shrunk: {len(public)} -> {public}"


# ── 5. _on_supertag_event 不抛异常 ─────────────────────────────────


def test_on_supertag_event_handles_payload_safely(tmp_path):
    ctx = _build_ctx(tmp_path)
    event = SimpleNamespace(
        event_type="supertag.created",
        payload={"id": "x1", "password": "should-be-masked"},
    )
    # 不应抛异常；password 字段被过滤是通过日志侧验证，这里只校验稳定性
    ctx._on_supertag_event(event)


# ── 6. base_dir 为 None 时回退到 "data" ────────────────────────────


def test_base_dir_defaults_to_data_when_none(tmp_path, monkeypatch):
    # 切到 tmp_path 作为工作目录，避免在 tmp_path/data 之外的真实路径下写文件
    monkeypatch.chdir(tmp_path)

    data_store = MagicMock(name="data_store")
    ai_client = MagicMock(name="ai_client")
    ctx = MemoryContext(
        data_store=data_store,
        data_base_dir=None,
        ai_client=ai_client,
        settings_repository=MagicMock(),
        user_profile_service=MagicMock(),
        note_repository=MagicMock(),
        task_repository=MagicMock(),
        inbox_repository=MagicMock(),
        hybrid_search_service=None,
    )
    assert ctx.knowledge_graph_store is not None
    assert ctx._hybrid_memory_search is not None

    # 清理 supertag 订阅
    from core.events import event_bus
    for evt in ("supertag.created", "supertag.updated", "supertag.deleted"):
        event_bus.off(evt, ctx._on_supertag_event)
