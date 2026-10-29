# -*- coding: utf-8 -*-
"""R2-D（2026-09-17）语义 / 契约治理批的契约测试。

锁死三处修复，防止回归：

1. D-a —— cron 冷启动补跑的 HEAVY 清单补齐**叙事/报告族**。旧清单只有
   ``tech_watch.collect`` / ``subagent.spawn``，早报之外的周报/日报/简报/进化
   报告生成族（分钟级 LLM 生成 + 全库扫描）在补跑时与秒级轻任务同优先级，
   会重演 J3 队头阻塞。真名取自注册真源 ``services/cron_actions.py``
   （``register_cron_action`` 调用点）与 ``backend/handlers/_info_proxy.py``。
2. D-d —— weather 错参契约诚实性：未知参数必须 -32602，而不是被 ``**kwargs``
   静默吞掉、退回默认城市并返回 success。
3. D-h / D-i —— 两份治理文档存在且含指定小节 / 三态表。

全部 tmp_path / 内存 fake 隔离，绝不触碰真实 data/。
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

from core.errors import ErrorCode, InvalidParamsError
from data.local_storage import LocalDataStore
from services.cron_service import (
    PRIORITY_CATCHUP,
    PRIORITY_CATCHUP_HEAVY,
    CronService,
    _CATCHUP_HEAVY_PREFIXES,
)

# R2-D 新增的叙事/报告族前缀（真名来自注册真源，见模块 docstring）
R2D_HEAVY_PREFIXES = (
    "report.",
    "evolution.scan_and_report",
    "knowledge.brief",
    "knowledge.synthesize",
    "system.error_digest",
)

# 真源中必须以 HEAVY 身份被覆盖的 action_id（逐条对应 §1 的前缀；前两个
# tech_watch.collect / subagent.spawn 是 N-A 原有项）
R2D_HEAVY_ACTION_IDS = (
    "report.daily_summary",
    "evolution.scan_and_report",
    "knowledge.brief",
    "knowledge.synthesize",
    "system.error_digest",
)

# 对照组：这些不是分钟级重活，不得被新前缀误匹配成 HEAVY
# （journal.daily_draft 是模板拼接 + 本地写，N-A 既有测试已用它作轻任务对照）
LIGHT_ACTION_IDS = (
    "system.memory_health",
    "system.self_maintenance",
    "system.auto_backup",
    "info.collect",
    "ai_chat.collect",
    "proactive.tick",
    "suggestion.daily",
    "evolution.daily_scan",
    "tech_watch.judge",
    "knowledge.md_daily",
    "journal.daily_draft",
)


class _SettingsRepo:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


class _RecordingQueue:
    """内存假队列：只记录 enqueue 参数，用于断言顺序与优先级。"""

    def __init__(self):
        self.enqueued: list[tuple[str, dict]] = []
        self._next_id = 0

    def enqueue(self, **kwargs):
        self._next_id += 1
        tid = f"fake-task-{self._next_id}"
        self.enqueued.append((tid, kwargs))
        return tid

    def postpone(self, task_id, delay_seconds):  # noqa: ARG002
        return True

    def promote(self, task_id, priority=1):  # noqa: ARG002
        return True


def _make_service(tmp_path, *, task_queue=None):
    return CronService(
        data_store=LocalDataStore(base_dir=str(tmp_path)),
        executor=lambda action_id, payload: {"ok": True},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
        task_queue=task_queue,
    )


# ── 1. D-a：HEAVY 清单包含叙事/报告族（前缀层） ──────────────────────


def test_heavy_prefixes_include_narrative_report_family():
    """每个 R2-D 前缀都必须真在 _CATCHUP_HEAVY_PREFIXES 元组里。"""
    for prefix in R2D_HEAVY_PREFIXES:
        assert prefix in _CATCHUP_HEAVY_PREFIXES, f"HEAVY 清单缺少 {prefix!r}"


def test_heavy_action_ids_match_prefixes_and_light_ones_do_not():
    """startswith 元组匹配：报告族命中 HEAVY，轻任务不被误命中。"""
    for action_id in R2D_HEAVY_ACTION_IDS:
        assert action_id.startswith(_CATCHUP_HEAVY_PREFIXES), f"{action_id} 应判为 HEAVY"
    for action_id in LIGHT_ACTION_IDS:
        assert not action_id.startswith(_CATCHUP_HEAVY_PREFIXES), f"{action_id} 被误判为 HEAVY"


def test_cron_actions_registry_confirms_heavy_action_names():
    """交叉验证真源：除 report.generate（RPC 面）外，前缀对应的 action 均已注册。"""
    from services.cron_actions import list_cron_actions

    registered = {item["action_id"] for item in list_cron_actions()}
    for action_id in R2D_HEAVY_ACTION_IDS:
        assert action_id in registered, f"{action_id} 不在 cron 注册真源中"


# ── 2. D-a：_catch_up_missed 的轻重分层对新增前缀自然生效 ─────────────


def test_catch_up_sorts_report_family_last_with_bottom_priority(tmp_path, monkeypatch):
    """一条轻任务 + 两条新增报告族重活：重活排补跑序列尾部且优先级垫底。"""
    monkeypatch.setenv("SIYI_CRON_CATCHUP_STAGGER", "10")
    queue = _RecordingQueue()
    service = _make_service(tmp_path, task_queue=queue)
    service.add_job("日报", "report.daily_summary", {}, run_at="00:00", repeat="daily")
    service.add_job("轻任务", "info.collect", {}, run_at="00:00", repeat="daily")
    service.add_job("早报扫描", "evolution.scan_and_report", {}, run_at="00:00", repeat="daily")

    service._catch_up_missed()

    order = [kw["capability_id"] for _tid, kw in queue.enqueued]
    assert order[0] == "info.collect", f"轻任务必须排在报告族之前: {order}"
    assert set(order[1:]) == {"report.daily_summary", "evolution.scan_and_report"}
    prios = {kw["capability_id"]: kw["priority"] for _tid, kw in queue.enqueued}
    assert prios["info.collect"] == PRIORITY_CATCHUP
    assert prios["report.daily_summary"] == PRIORITY_CATCHUP_HEAVY
    assert prios["evolution.scan_and_report"] == PRIORITY_CATCHUP_HEAVY
    assert PRIORITY_CATCHUP_HEAVY < PRIORITY_CATCHUP, "垫底语义：HEAVY 优先级必须低于常规补跑"


# ── 3. D-d：weather 未知参数 → -32602（dispatch 层 + handler 直调） ───


def _weather_registry():
    from backend.handlers._weather import register_weather
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    ctx = SimpleNamespace(
        weather_service=SimpleNamespace(
            _default_city="beijing",
            _fetch_live_weather=lambda city, label: {
                "title": f"{city} · 晴",
                "value": "22C",
                "meta": "...",
                "tip": "...",
            },
        )
    )
    register_weather(registry, ctx)
    return registry, ctx


def test_weather_unknown_param_rejected_at_dispatch_layer():
    """证明层级：注册层 JSON Schema（additionalProperties=False）在 dispatch 前拦。"""
    registry, _ctx = _weather_registry()
    with pytest.raises(InvalidParamsError) as exc_info:
        asyncio.run(registry.dispatch("weather.current", {"citx": "shanghai"}))
    assert exc_info.value.code == ErrorCode.INVALID_PARAMS == -32602
    # _format_validation_error 对 additionalProperties 的定型文案
    assert "Unexpected additional field" in str(exc_info.value.details)


def test_weather_unknown_param_rejected_on_direct_handler_call():
    """证明层级：绕过 registry 的 handler 直调同样拒绝（不依赖注册层）。"""
    registry, _ctx = _weather_registry()
    handler = registry._handlers["weather.current"]
    with pytest.raises(InvalidParamsError) as exc_info:
        asyncio.run(handler(city="beijing", location="shanghai"))
    assert exc_info.value.code == -32602
    assert exc_info.value.details["unexpected"] == ["location"]


def test_weather_valid_and_omitted_city_paths_unchanged():
    """只有完全不传城市参数时才允许默认城市；显式 city 必须被采用。"""
    registry, _ctx = _weather_registry()

    omitted = asyncio.run(registry.dispatch("weather.current", {}))
    assert omitted["success"] is True and omitted["title"].startswith("beijing")

    explicit = asyncio.run(registry.dispatch("weather.current", {"city": "shanghai"}))
    assert explicit["success"] is True and explicit["title"].startswith("shanghai")


# ── 4. D-h / D-i：两份治理文档存在且含指定内容 ─────────────────────────


def test_kernel_contract_has_f1_loopback_threat_model_section():
    text = (APP_ROOT.parent / "docs" / "kernel-contract.md").read_text(encoding="utf-8")
    assert "### 4.5 F-1 回环自批准威胁模型" in text
    assert "trusted" in text and "不防本机同用户进程" in text


def test_orphan_adjudication_doc_exists_with_three_states():
    text = (APP_ROOT.parent / "deliverables" / "orphan-adjudication-2026-09-17.md").read_text(encoding="utf-8")
    assert "已裁决" in text and "保留在主窗" in text and "待裁决" in text
    for method in (
        "job_hunter.jobs.get",
        "job_hunter.jobs.fetch_detail",
        "job_hunter.scrape.status",
        "info.sources.add",
        "info.sources.toggle",
        "notification.mark_read",
    ):
        assert method in text, f"孤儿裁决清单缺少 {method}"
