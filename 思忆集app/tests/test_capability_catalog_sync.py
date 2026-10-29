"""回归测试：工具目录同步与装配补齐（2026-09-11）。

背景（生产 `trace.db` 实证）：

- butler（PydanticAI）的工具清单来自 ``services.react_tool_catalog.REACT_TOOL_CATALOG``
  （51 条，**含** ``notes.*`` / ``tasks.*`` / ``inbox.*``）。
- 但 ``AssistantCapabilityRouter`` 的 capability registry 只从
  ``AssistantPlannerLayer.CAPABILITY_CATALOG``（84 条，**不含**这些条目）注册。
- 两个目录不同步 → 模型看得见工具、ActionBroker 却查不到对应 capability →
  直接以 ``capability_not_found`` 拦死（原文："能力 'notes.list' 未注册"）。

本文件把这些结论固化为机器断言，防止目录再次漂移。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.capability_router import AssistantCapabilityRouter
from data.local_storage import LocalDataStore
from services.capability_types import CapabilityRegistry


def _build_router_with_registry(tmp_path):
    """构造带 capability_registry 的 router（否则 _register_builtin_capabilities 直接返回）。"""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    store = LocalDataStore(base_dir=str(runtime_root))
    registry = CapabilityRegistry()
    router = AssistantCapabilityRouter(
        data_store=store,
        settings_repository=None,
        observability_service=None,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        inbox_repository=None,
        capability_registry=registry,
    )
    return router, registry


@pytest.mark.parametrize(
    "action_id",
    [
        "notes.list",
        "notes.search",
        "notes.create",
        "notes.get",
        "notes.update",
        "tasks.list",
        "tasks.create",
        "inbox.list",
        "inbox.add",
    ],
)
def test_react_catalog_capabilities_are_registered(tmp_path, action_id):
    """REACT_TOOL_CATALOG 覆盖的能力必须进入 broker 的 registry。

    这些条目只存在于 REACT_TOOL_CATALOG，不在 planner catalog 中——
    修复前它们永远注册不上，模型一调用就被 ActionBroker 拒绝。
    """
    _, registry = _build_router_with_registry(tmp_path)
    assert registry.get_capability(action_id) is not None, f"{action_id} 未被注册"


def test_backfilled_capabilities_use_prefix_risk_levels(tmp_path):
    """补齐注册的 risk_level 应与 ReAct 路径的前缀推断保持一致。"""
    _, registry = _build_router_with_registry(tmp_path)
    destructive = registry.get_capability("notes.delete")
    read_only = registry.get_capability("notes.list")
    assert destructive is not None, "notes.delete 未被注册"
    assert read_only is not None, "notes.list 未被注册"
    assert destructive.risk_level == "destructive"
    assert read_only.risk_level == "read"


def test_backfilled_capabilities_carry_payload_schema(tmp_path):
    """补齐的能力应带上 REACT_TOOL_CATALOG 的 payload_schema（供 broker 校验入参）。"""
    _, registry = _build_router_with_registry(tmp_path)
    spec = registry.get_capability("notes.list")
    assert spec is not None
    assert spec.payload_schema, "notes.list 缺少 payload_schema"


# ── 装配补齐：memory_index_service 必须传到 router（2026-09-11）──────────────
# 此前 autonomous.py 把 memory_index_service 交给 build_into，但 LifecycleBuilder
# 构造 AssistantCapabilityRouter 时漏传 → router.memory_index_service 恒为 None
# → memory.recall 直接返回 memory_index_unavailable（生产 trace.db 实证）。


def test_lifecycle_builder_passes_memory_index_service(tmp_path, monkeypatch):
    """LifecycleBuilder 构造 router 时必须透传 memory_index_service。"""
    from types import SimpleNamespace

    import assistant.autonomy_components.lifecycle_builder as lb

    sentinel = object()
    captured: dict = {}
    original = lb.AssistantCapabilityRouter

    def _spy(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(lb, "AssistantCapabilityRouter", _spy)

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    store = LocalDataStore(base_dir=str(runtime_root))

    lb.AutonomyLifecycleBuilder.build_into(
        SimpleNamespace(),
        data_store=store,
        settings_repository=None,
        observability_service=None,
        note_repository=None,
        task_repository=None,
        inbox_repository=None,
        memory_index_service=sentinel,
    )

    assert "memory_index_service" in captured, "构造 router 时未传 memory_index_service"
    assert captured["memory_index_service"] is sentinel


def test_capability_router_exposes_memory_index_service(tmp_path):
    """router 应把注入的 memory_index_service 暴露为同名属性（_knowledge 依赖它）。"""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    store = LocalDataStore(base_dir=str(runtime_root))
    sentinel = object()
    router = AssistantCapabilityRouter(
        data_store=store,
        settings_repository=None,
        observability_service=None,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        inbox_repository=None,
        memory_index_service=sentinel,
    )
    assert router.memory_index_service is sentinel


# ── 进程内端到端：复现生产失败的原路径 ──────────────────────────────────────
# action_broker.py:214-222 —— broker 先查 registry，查不到就
# _build_blocked("capability_not_found")。生产 trace.db 里 notes.list 正死在这里。


def test_notes_list_passes_action_broker_capability_check(tmp_path):
    """notes.list 经 ActionBroker 校验时不得再报 capability_not_found。

    这是修复前**生产实际失败的那条路径**的进程内复现。
    """
    from services.action_broker import ActionRequest

    router, _ = _build_router_with_registry(tmp_path)
    broker = router.action_broker
    assert broker is not None, "router 应自动创建 ActionBroker"

    result = broker.execute_sync(ActionRequest(capability_id="notes.list", input={"limit": 5}, source="assistant"))
    assert result.blocked_reason != "capability_not_found", f"notes.list 仍被 broker 以未注册拦死：{result.blocked_reason} / {result.error}"


def test_memory_recall_passes_action_broker_capability_check(tmp_path):
    """memory.recall 同理——它同时受「目录未注册」与「记忆服务未注入」两个断点影响。"""
    from services.action_broker import ActionRequest

    router, _ = _build_router_with_registry(tmp_path)
    broker = router.action_broker
    assert broker is not None

    result = broker.execute_sync(ActionRequest(capability_id="memory.recall", input={"query": "笔记"}, source="assistant"))
    assert result.blocked_reason != "capability_not_found", f"memory.recall 仍被 broker 以未注册拦死：{result.blocked_reason} / {result.error}"
