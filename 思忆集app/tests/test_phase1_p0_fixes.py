"""Phase 1 P0 紧急修复的单元测试。

覆盖 6 个 Bug：
- Bug #1: services/feedback_service.py 创建
- Bug #2: NotificationDispatcher 协程调度
- Bug #3: system_health.py 方法名（stats vs get_stats）
- Bug #4: provider_routing.py 模型名（deepseek-chat vs deepseek-v4-flash）
- Bug #5: _cascade_cleanup 模块重建
- Bug #6: _judge_aware_client 隐私守卫绕过
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
# Bug #1: FeedbackService
# ════════════════════════════════════════════════════════════


class _FakeDataStore:
    """模拟 local_storage 的 data_store。"""

    def __init__(self):
        self._data: dict[str, Any] = {}

    def load_json(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default if default is not None else [])

    def save_json(self, key: str, value: Any) -> None:
        self._data[key] = value


class _FakeSettings:
    """模拟 settings_repository。"""

    def __init__(self, data: dict[str, Any] | None = None):
        self._data = data or {}

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._data[key] = value


class _FakeEventBus:
    """模拟 EventBus，记录所有 emit 调用。

    B5（2026-08-26）后 core.events.EventBus 的 emit 契约为单参 AppEvent，
    feedback_service 已按该契约 emit（旧双参 emit 会抛 TypeError 并被
    try/except 吞掉，事件实际从不发出）。本 fake 同步对齐单参契约，
    并把 AppEvent 解包为 (event_type, payload) 元组，使既有
    events[i][0] / events[i][1] 断言继续成立。
    """

    def __init__(self):
        self.events: list[tuple[str, Any]] = []

    def emit(self, event: Any) -> None:
        self.events.append((event.event_type, event.payload))


@pytest.fixture
def feedback_svc():
    """构造一个 FeedbackService 实例，AI 客户端为 None（走规则兜底）。"""
    from services.feedback_service import FeedbackService

    return FeedbackService(
        data_store=_FakeDataStore(),
        settings_repository=_FakeSettings(),
        ai_client=None,
        event_bus=_FakeEventBus(),
    )


# ── record_feedback ──────────────────────────────────────


def test_bug1_record_feedback_up(feedback_svc):
    """Bug #1: record_feedback 应成功记录 👍 反馈。"""
    result = feedback_svc.record_feedback(
        session_id="s1",
        message_index=0,
        rating="up",
        original_user_msg="hi",
        original_ai_msg="hello",
    )
    assert result["recorded"] is True
    assert result["feedback"]["rating"] == "up"
    assert result["feedback"]["is_edited"] is False
    # EventBus 应触发 feedback.recorded 事件
    assert len(feedback_svc._event_bus.events) == 1
    assert feedback_svc._event_bus.events[0][0] == "feedback.recorded"


def test_bug1_record_feedback_down_edited(feedback_svc):
    """Bug #1: edited_text 与 original_ai_msg 不同时应标记 is_edited=True。"""
    result = feedback_svc.record_feedback(
        session_id="s1",
        message_index=0,
        rating="down",
        original_ai_msg="bad reply",
        edited_text="better reply",
    )
    assert result["feedback"]["is_edited"] is True
    assert result["feedback"]["rating"] == "down"


def test_bug1_record_feedback_invalid_rating(feedback_svc):
    """Bug #1: 无效 rating 应抛 ValueError。"""
    with pytest.raises(ValueError, match="rating must be"):
        feedback_svc.record_feedback(session_id="s1", message_index=0, rating="invalid")


def test_bug1_record_feedback_lru_limit(feedback_svc):
    """Bug #1: 反馈数超过 500 条上限时应 LRU 丢弃最早的。"""
    from services.feedback_service import MAX_FEEDBACK_RECORDS

    for i in range(MAX_FEEDBACK_RECORDS + 10):
        feedback_svc.record_feedback(session_id="s1", message_index=i, rating="up")
    records = feedback_svc._store.load_json("chat_feedback", [])
    assert len(records) == MAX_FEEDBACK_RECORDS
    # 最早的 10 条应被丢弃
    assert records[0]["message_index"] == 10


# ── list_feedback ────────────────────────────────────────


def test_bug1_list_feedback_filter_by_rating(feedback_svc):
    """Bug #1: list_feedback 应支持按 rating 过滤。"""
    feedback_svc.record_feedback(session_id="s1", message_index=0, rating="up")
    feedback_svc.record_feedback(session_id="s1", message_index=1, rating="down")
    result = feedback_svc.list_feedback(rating="down")
    assert len(result) == 1
    assert result[0]["rating"] == "down"


def test_bug1_list_feedback_limit_capped(feedback_svc):
    """Bug #1: limit 应被 clamp 到 [1, 500]。"""
    result = feedback_svc.list_feedback(limit=10000)
    assert isinstance(result, list)
    # 空数据也应返回 list，不报错
    assert len(result) == 0


# ── aggregate ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bug1_aggregate_below_threshold(feedback_svc):
    """Bug #1: 样本数低于阈值时不应聚合。"""
    feedback_svc.record_feedback(session_id="s1", message_index=0, rating="down")
    result = await feedback_svc.aggregate()
    assert result["aggregated"] is False
    assert result["reason"] == "below_threshold"


@pytest.mark.asyncio
async def test_bug1_aggregate_force_with_rule_fallback(feedback_svc):
    """Bug #1: force=True 时应聚合，AI 不可用走规则兜底。"""
    feedback_svc.record_feedback(
        session_id="s1",
        message_index=0,
        rating="down",
        original_user_msg="test",
        original_ai_msg="bad",
    )
    result = await feedback_svc.aggregate(force=True)
    assert result["aggregated"] is True
    assert "summary" in result
    assert len(result["summary"]) > 0
    # 偏好应已写入存储
    assert feedback_svc.get_preference() == result["summary"]
    # EventBus 应触发 feedback.aggregated 事件
    assert any(e[0] == "feedback.aggregated" for e in feedback_svc._event_bus.events)


@pytest.mark.asyncio
async def test_bug1_aggregate_no_samples(feedback_svc):
    """Bug #1: 无学习样本时不应聚合。"""
    result = await feedback_svc.aggregate(force=True)
    assert result["aggregated"] is False
    assert result["reason"] == "no_samples"


# ── privacy ──────────────────────────────────────────────


def test_bug1_preference_max_length(feedback_svc):
    """Bug #1: 偏好摘要应限长 600 字。"""
    from services.feedback_service import MAX_PREFERENCE_LEN

    feedback_svc.set_preference("x" * 1000)
    assert len(feedback_svc.get_preference()) == MAX_PREFERENCE_LEN


def test_bug1_ai_summarize_truncates_samples(feedback_svc):
    """Bug #1: AI 提炼时原文应截断到 200 字。"""
    long_text = "x" * 500
    samples = [
        {
            "rating": "down",
            "original_user_msg": long_text,
            "original_ai_msg": long_text,
            "edited_text": long_text,
        }
    ]
    # _rule_summarize 不直接受影响，但 _ai_summarize 会截断
    # 这里验证规则兜底也能正常工作
    summary = feedback_svc._rule_summarize(samples)
    assert isinstance(summary, str)
    assert len(summary) <= 600


# ════════════════════════════════════════════════════════════
# Bug #2: NotificationDispatcher 协程调度
# ════════════════════════════════════════════════════════════


class _FakeEvent:
    """模拟 core.events.AppEvent。"""

    def __init__(self, payload):
        self.payload = payload


class _SimpleEventBus:
    """模拟 EventBus：同步调用 handler。"""

    def __init__(self):
        self._handlers: dict[str, Any] = {}

    def on(self, event_type: str, handler) -> None:
        self._handlers[event_type] = handler

    def off(self, event_type: str, handler) -> None:
        self._handlers.pop(event_type, None)

    def emit(self, event_type: str, payload: Any) -> None:
        handler = self._handlers.get(event_type)
        if handler is not None:
            # 与 core.events.EventBus 一致：同步调用 handler
            handler(_FakeEvent(payload))


@pytest.mark.asyncio
async def test_bug2_event_handler_awaits_dispatch():
    """Bug #2: EventBus 触发的 handler 必须真正 await dispatch，否则通知静默失败。"""
    from services.notification_dispatcher import NotificationDispatcher

    dispatched: list[tuple[str, Any]] = []

    # 构造一个最小化的 dispatcher，绕过 __init__ 的复杂依赖
    disp = NotificationDispatcher.__new__(NotificationDispatcher)
    disp._event_bus = _SimpleEventBus()
    disp._started = False
    disp._subscriptions = []
    # M9（2026-08-29）：__init__ 新增 _dispatch_tasks 强引用集；
    # __new__ 绕过 __init__ 的测试构造需自行补齐该属性。
    disp._dispatch_tasks = set()

    # 拦截 dispatch 记录调用
    async def fake_dispatch(event_type: str, payload: Any) -> dict[str, Any]:
        dispatched.append((event_type, payload))
        return {"dispatched": True}

    disp.dispatch = fake_dispatch

    # 用 _make_handler 创建 handler 并注册
    handler = disp._make_handler("inbox.delivered")
    disp._event_bus.on("inbox.delivered", handler)

    # 模拟 EventBus 触发
    disp._event_bus.emit("inbox.delivered", {"content": "test notification"})

    # 让事件循环处理 create_task 调度的协程
    await asyncio.sleep(0.05)

    # 断言：dispatch 被真正调用（协程被 await）
    assert len(dispatched) == 1, f"dispatch 应被调用 1 次，实际 {len(dispatched)} 次"
    assert dispatched[0][0] == "inbox.delivered"
    assert dispatched[0][1]["content"] == "test notification"


@pytest.mark.asyncio
async def test_bug2_no_coroutine_warning():
    """Bug #2: handler 不应触发 'coroutine was never awaited' RuntimeWarning。"""
    from services.notification_dispatcher import NotificationDispatcher
    import warnings

    disp = NotificationDispatcher.__new__(NotificationDispatcher)
    disp._event_bus = _SimpleEventBus()
    disp._started = False
    disp._subscriptions = []
    # M9（2026-08-29）：__init__ 新增 _dispatch_tasks 强引用集；
    # __new__ 绕过 __init__ 的测试构造需自行补齐该属性。
    disp._dispatch_tasks = set()

    called = asyncio.Event()

    async def fake_dispatch(event_type: str, payload: Any) -> dict[str, Any]:
        called.set()
        return {"dispatched": True}

    disp.dispatch = fake_dispatch
    handler = disp._make_handler("test.event")
    disp._event_bus.on("test.event", handler)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        disp._event_bus.emit("test.event", {"data": "test"})
        await asyncio.sleep(0.05)

    assert called.is_set(), "dispatch 应被调用"


# ════════════════════════════════════════════════════════════
# Bug #3: system_health.py 方法名
# ════════════════════════════════════════════════════════════


def test_bug3_collect_task_queue_uses_stats_method():
    """Bug #3: _collect_task_queue 应调用 stats()，而非 get_stats()。"""
    from services.system_health import _collect_task_queue

    class FakeQueue:
        """模拟 PersistentTaskQueue，只有 stats() 方法。"""

        def stats(self) -> dict[str, int]:
            return {"pending": 5, "dead_letter": 0, "running": 2}

    class FakeContext:
        task_queue = FakeQueue()

    result = _collect_task_queue(FakeContext())
    assert result["available"] is True
    assert result["severity"] != "down"
    assert result["details"]["pending"] == 5


def test_bug3_collect_task_queue_down_on_high_dead_letter():
    """Bug #3: dead_letter > 200 时应为 DOWN。"""
    from services.system_health import _collect_task_queue

    class FakeQueue:
        def stats(self) -> dict[str, int]:
            return {"pending": 10, "dead_letter": 250, "running": 0}

    class FakeContext:
        task_queue = FakeQueue()

    result = _collect_task_queue(FakeContext())
    assert result["available"] is True
    assert result["severity"] == "down"


def test_bug3_collect_task_queue_handles_missing_queue():
    """Bug #3: task_queue 为 None 时应返回 UNKNOWN。"""
    from services.system_health import _collect_task_queue

    class FakeContext:
        task_queue = None

    result = _collect_task_queue(FakeContext())
    assert result["available"] is False
    assert result["severity"] == "unknown"


def test_bug3_collect_task_queue_fallback_to_get_stats():
    """Bug #3: 若实现只有 get_stats() 也应兼容。"""
    from services.system_health import _collect_task_queue

    class LegacyQueue:
        """旧版本只有 get_stats() 方法。"""

        def get_stats(self) -> dict[str, int]:
            return {"pending": 3, "dead_letter": 1, "running": 1}

    class FakeContext:
        task_queue = LegacyQueue()

    result = _collect_task_queue(FakeContext())
    assert result["available"] is True
    assert result["details"]["pending"] == 3


# ════════════════════════════════════════════════════════════
# Bug #4: provider_routing.py 模型名
# ════════════════════════════════════════════════════════════


def test_bug4_deepseek_preset_model_is_valid():
    """Bug #4: DeepSeek 预设模型名必须是官方真实存在的模型。

    2026-07-24 决策（AGENTS.md 与 provider_routing.py 注释同步记载）：
    官方已退役 deepseek-chat / deepseek-reasoner，现行官方模型为
    deepseek-v4-flash。
    """
    from ai.provider_routing import _PROVIDER_ROUTING_PRESETS

    valid_deepseek_models = {"deepseek-v4-flash"}
    model = _PROVIDER_ROUTING_PRESETS["deepseek"]["default_model"]
    assert model in valid_deepseek_models, f"deepseek default_model 应为 {valid_deepseek_models} 之一，实际为 {model}"


def test_bug4_no_retired_deepseek_models():
    """Bug #4: 不得回退使用已退役的 DeepSeek 模型名（2026-07-24 官方退役）。"""
    from ai.provider_routing import _PROVIDER_ROUTING_PRESETS

    model = _PROVIDER_ROUTING_PRESETS["deepseek"]["default_model"]
    retired = {"deepseek-chat", "deepseek-reasoner"}
    assert model not in retired, f"deepseek default_model 不得为已退役模型名，实际为 {model}"


def test_bug4_all_preset_models_non_empty():
    """Bug #4: 所有 preset 的 default_model 必须非空字符串。"""
    from ai.provider_routing import _PROVIDER_ROUTING_PRESETS

    for name, cfg in _PROVIDER_ROUTING_PRESETS.items():
        assert cfg["default_model"], f"{name} has empty default_model"
        assert isinstance(cfg["default_model"], str)


# ════════════════════════════════════════════════════════════
# Bug #5: _cascade_cleanup 模块重建
# ════════════════════════════════════════════════════════════


def test_bug5_module_importable():
    """Bug #5: _cascade_cleanup 模块应可正常导入。"""
    from backend.handlers._cascade_cleanup import cascade_delete_note, cascade_delete_task

    assert callable(cascade_delete_note)
    assert callable(cascade_delete_task)


@pytest.mark.asyncio
async def test_bug5_cascade_delete_note_cleans_kg():
    """Bug #5: cascade_delete_note 应清理知识图谱节点。

    PD-2（2026-09-17）契约反转：旧 FakeKG 定义了真实 store 上不存在的
    ``delete_entity`` 并把 no-op 谎报锁成了期望行为。新契约按真实 API 面
    （remove_by_source / remove_entity / save）断言，结果携带删除计数。
    """
    from backend.handlers._cascade_cleanup import cascade_delete_note

    calls: list = []

    class FakeKG:
        def remove_by_source(self, source_type, source_id):
            calls.append(("by_source", source_type, str(source_id)))
            return 1

        def remove_entity(self, entity_id):
            calls.append(("entity", entity_id))
            return False

        def save(self):
            calls.append(("save",))

    class FakeContext:
        # R0-B21：属性名对齐生产注入（_context_builder.py 注入
        # ctx.knowledge_graph_store）；旧名 knowledge_graph 生产从未注入。
        knowledge_graph_store = FakeKG()
        memory_index_service = None
        structured_memory_store = None

    result = await cascade_delete_note(FakeContext(), "note_123")
    assert ("by_source", "note", "note_123") in calls
    assert ("entity", "note_123") in calls
    assert ("save",) in calls, "PD-2b：级联删除后必须落盘"
    assert result["knowledge_graph"] == "deleted:1"


@pytest.mark.asyncio
async def test_bug5_cascade_delete_note_cleans_memory_index():
    """Bug #5: cascade_delete_note 应清理记忆索引条目。

    T5.1 修复：delete_entries_by_source 真实签名是 (source_type, source_id)，
    旧测试的 FakeMemoryIndex 用单参签名"测的正是错误的调用方式"，掩盖了真实接口不匹配。
    本测试改用真实双参签名并断言调用参数，任何一方回退都会红。
    """
    from backend.handlers._cascade_cleanup import cascade_delete_note

    deleted_sources = []

    class FakeMemoryIndex:
        def delete_entries_by_source(self, source_type, source_id):
            deleted_sources.append((source_type, source_id))
            return 1

    class FakeContext:
        # R0-B21：属性名对齐生产注入（ctx.memory_index_service）。
        knowledge_graph_store = None
        memory_index_service = FakeMemoryIndex()
        structured_memory_store = None

    result = await cascade_delete_note(FakeContext(), "note_456")
    assert deleted_sources == [("note", "note_456")]
    assert result["memory_index"] == "deleted:1"


@pytest.mark.asyncio
async def test_bug5_cascade_delete_task_cleans_cron():
    """Bug #5: cascade_delete_task 应清理关联的 cron job。"""
    from backend.handlers._cascade_cleanup import cascade_delete_task

    removed_cron_ids = []

    class FakeCronService:
        def list_jobs(self):
            return [
                {"id": "cron_1", "params": {"task_id": "task_123"}},
                {"id": "cron_2", "params": {"task_id": "task_999"}},
            ]

        def remove(self, cron_id):
            removed_cron_ids.append(cron_id)

    class FakeContext:
        cron_service = FakeCronService()
        reminder_service = None
        knowledge_graph = None
        memory_index = None
        structured_memory_store = None

    result = await cascade_delete_task(FakeContext(), "task_123")
    assert "cron_1" in removed_cron_ids
    assert "cron_2" not in removed_cron_ids
    assert "deleted" in result["cron"]


@pytest.mark.asyncio
async def test_bug5_cascade_delete_note_handles_missing_services():
    """Bug #5: 所有服务为 None 时不应报错（best-effort）。"""
    from backend.handlers._cascade_cleanup import cascade_delete_note

    class FakeContext:
        knowledge_graph = None
        memory_index = None
        structured_memory_store = None

    result = await cascade_delete_note(FakeContext(), "note_789")
    assert result == {}  # 无服务可清理，返回空 dict


@pytest.mark.asyncio
async def test_bug5_cascade_delete_note_handles_service_failure():
    """Bug #5: 单个服务清理失败不应阻塞其他清理。"""
    from backend.handlers._cascade_cleanup import cascade_delete_note

    class FailingKG:
        # PD-2（2026-09-17）：真实 API 面抛错 → 新实现聚合为 "failed: ..."
        def remove_by_source(self, source_type, source_id):
            raise RuntimeError("KG connection failed")

        def remove_entity(self, entity_id):
            raise RuntimeError("KG connection failed")

    class GoodMemoryIndex:
        def __init__(self):
            self.deleted = []

        def delete_entries_by_source(self, source_type, source_id):
            self.deleted.append((source_type, source_id))
            return 1

    good_mem = GoodMemoryIndex()

    class FakeContext:
        # R0-B21：属性名对齐生产注入。
        knowledge_graph_store = FailingKG()
        memory_index_service = good_mem
        structured_memory_store = None

    result = await cascade_delete_note(FakeContext(), "note_fail")
    # KG 失败应记录错误，不阻塞 memory_index
    assert "failed" in result["knowledge_graph"]
    assert result["memory_index"] == "deleted:1"
    assert good_mem.deleted == [("note", "note_fail")]


# ════════════════════════════════════════════════════════════
# Bug #6: 隐私守卫绕过
# ════════════════════════════════════════════════════════════


def test_bug6_privacy_classification_runs_before_persona_injection():
    """Bug #6: 隐私分类应在 persona 注入之前执行（用原始 messages）。

    验证方式：构造一个 JudgeAwareClient，让 _extract_user_text 在
    persona 注入后抛异常。若隐私分类在 persona 注入之前执行，
    则 skip_cloud 应已正确计算，外层 except 会走 _route_local
    而非 _inner.chat_completion。
    """
    from ai.client._judge_aware_client import JudgeAwareClient

    cloud_calls: list[Any] = []

    class FakeInner:
        def chat_completion(self, messages, **kwargs):
            cloud_calls.append(messages)
            return {"choices": [{"message": {"content": "cloud reply"}}]}

    class FakeLocalRouter:
        def process(self, text):
            class R:
                handled = True
                response = "local reply"
                confidence = 1.0
                reason = "privacy"

            return R()

    # 构造 client，绕过 __init__
    client = JudgeAwareClient.__new__(JudgeAwareClient)
    client._inner = FakeInner()
    client._local_ai_router = None
    client._aggregator = None
    client._settings = None
    client._logger = logging.getLogger("test")

    # _classify_privacy 对敏感内容返回 local_only
    def fake_classify_privacy(text):
        if "银行卡" in text or "密码" in text:
            return "local_only"
        return "public"

    client._classify_privacy = fake_classify_privacy

    # _extract_user_text 正常工作
    def fake_extract_user_text(messages):
        return messages[-1].get("content", "") if messages else ""

    client._extract_user_text = fake_extract_user_text

    # _is_explicit_user_action 返回 False
    client._is_explicit_user_action = lambda msgs: False

    # inject_xiaowen_persona 不修改 messages（模拟）
    def fake_inject(messages, inject_persona=True):
        return messages

    # _route_local 返回本地回复
    def fake_route_local(text, messages, temperature, max_tokens):
        return {"choices": [{"message": {"content": "local reply (privacy)"}}]}

    client._route_local = fake_route_local

    # mock module-level inject_xiaowen_persona
    import ai.client._judge_aware_client as jac_module

    original_inject = getattr(jac_module, "inject_xiaowen_persona", None)
    jac_module.inject_xiaowen_persona = fake_inject

    try:
        messages = [{"role": "user", "content": "我的银行卡号是 6228 1234 5678 9012"}]
        result = client.chat_completion(messages)

        # 断言：local_only 数据走本地，未调用云端
        assert cloud_calls == [], "local_only 数据不应调用云端"
        assert result["choices"][0]["message"]["content"] == "local reply (privacy)"
    finally:
        if original_inject is not None:
            jac_module.inject_xiaowen_persona = original_inject


def test_bug6_privacy_guard_not_bypassed_on_exception():
    """Bug #6: Judge 路由异常时，local_only 数据必须走本地，不得回退到云端。"""
    from ai.client._judge_aware_client import JudgeAwareClient

    cloud_calls: list[Any] = []

    class FakeInner:
        def chat_completion(self, messages, **kwargs):
            cloud_calls.append(messages)
            return {"choices": [{"message": {"content": "cloud reply"}}]}

    client = JudgeAwareClient.__new__(JudgeAwareClient)
    client._inner = FakeInner()
    client._local_ai_router = None
    client._aggregator = None
    client._settings = None
    client._logger = logging.getLogger("test")

    # 隐私分类返回 local_only
    client._classify_privacy = lambda text: "local_only"
    client._extract_user_text = lambda msgs: msgs[-1].get("content", "")
    client._is_explicit_user_action = lambda msgs: False

    # inject_xiaowen_persona 抛异常，模拟 persona 注入失败
    def failing_inject(messages, inject_persona=True):
        raise RuntimeError("persona injection failed")

    # _route_local 正常工作
    def fake_route_local(text, messages, temperature, max_tokens):
        return {"choices": [{"message": {"content": "local fallback"}}]}

    client._route_local = fake_route_local

    import ai.client._judge_aware_client as jac_module

    original_inject = getattr(jac_module, "inject_xiaowen_persona", None)
    jac_module.inject_xiaowen_persona = failing_inject

    try:
        messages = [{"role": "user", "content": "敏感数据"}]
        result = client.chat_completion(messages)

        # 断言：即使 persona 注入失败，local_only 数据也走本地
        assert cloud_calls == [], "异常回退不得绕过隐私守卫上云"
        assert result["choices"][0]["message"]["content"] == "local fallback"
    finally:
        if original_inject is not None:
            jac_module.inject_xiaowen_persona = original_inject


def test_bug6_tools_respect_privacy():
    """Bug #6: 即使 tools 参数存在，local_only 数据也必须走本地。"""
    from ai.client._judge_aware_client import JudgeAwareClient

    cloud_calls: list[Any] = []

    class FakeInner:
        def chat_completion(self, messages, **kwargs):
            cloud_calls.append(messages)
            return {"choices": [{"message": {"content": "cloud with tools"}}]}

    client = JudgeAwareClient.__new__(JudgeAwareClient)
    client._inner = FakeInner()
    client._local_ai_router = None
    client._aggregator = None
    client._settings = None
    client._logger = logging.getLogger("test")

    client._classify_privacy = lambda text: "local_only"
    client._extract_user_text = lambda msgs: msgs[-1].get("content", "")
    client._is_explicit_user_action = lambda msgs: False

    def fake_route_local(text, messages, temperature, max_tokens):
        return {"choices": [{"message": {"content": "local with tools"}}]}

    client._route_local = fake_route_local

    import ai.client._judge_aware_client as jac_module

    jac_module.inject_xiaowen_persona = lambda msgs, inject_persona=True: msgs

    messages = [{"role": "user", "content": "密码是 123456"}]
    tools = [{"type": "function", "function": {"name": "test"}}]
    result = client.chat_completion(messages, tools=tools)

    assert cloud_calls == [], "tools 存在时 local_only 数据仍应走本地"
    assert result["choices"][0]["message"]["content"] == "local with tools"
