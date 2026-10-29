# -*- coding: utf-8 -*-
"""M1 尾：路由决策最小埋点（§9.5⑰）+ not_configured 分型漏判（e2e 登记项 4）。

任务一：每次 chat 路由判定向既有 ``usage_records`` 写一行决策标记
（provider='router' / model='decision' / source='route:fast|route:deep'），
使 ``scripts/route_report.py`` 的 fast_ratio 从 null 变为可算，且**不污染**
D20 观察窗的 kernel 样本（provider 不以 'kernel' 开头）。

任务二：一个 provider 都没配时到终端的文案是回退链耗尽文案
（「AI 服务不可用，且未命中本地回退」/「流式对话在所有提供方均失败」），
旧 ``_NOT_CONFIGURED_MARKERS`` 认不出 → 误分型 unknown_error。

任务三：route_report 兼容回归——以函数参数注入临时库，断言 ratio>0 且
kernel_calls 未被决策行抬高（绝不触碰 思忆集app/data/ 真实库）。
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

import ai.usage_tracker as ut
from ai.types import AIServiceError
from ai.usage_tracker import UsageTracker
from backend.handlers.chat_paths import gateway_path as gp
from backend.handlers.chat_paths.gateway_path import _use_gateway_route
from backend.handlers.chat_paths.helpers import grade_stream_exception

_APP_ROOT = Path(__file__).resolve().parents[1]
_ROUTE_REPORT_PATH = _APP_ROOT / "scripts" / "route_report.py"


@pytest.fixture(scope="module")
def route_report():
    """按文件路径加载 scripts/route_report.py（正源读数口，不注入 sys.path）。"""
    spec = importlib.util.spec_from_file_location("route_report_script", _ROUTE_REPORT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("route_report_script", module)
    spec.loader.exec_module(module)
    return module


class _LaneRecorder:
    """替身 tracker：只收集 record_route_decision 的档位。"""

    def __init__(self):
        self.lanes: list[str] = []

    def record_route_decision(self, lane: str) -> None:
        self.lanes.append(lane)


def _ctx(chat_route: str = "legacy", kernel=None) -> types.SimpleNamespace:
    settings = types.SimpleNamespace(
        get_setting=lambda key, default=None: chat_route if key == "chat_route" else default
    )
    return types.SimpleNamespace(agent_gateway=kernel, settings_repository=settings)


# ── 任务一：决策标记的写入口径 ─────────────────────────────────


def test_record_route_decision_writes_exact_marker_row(tmp_path):
    t = UsageTracker(str(tmp_path / "ai_usage.db"))
    t.record_route_decision("fast")
    rec = t.get_records(limit=5)[0]
    assert rec["provider"] == "router"
    assert rec["model"] == "decision"
    assert rec["source"] == "route:fast"
    assert (rec["prompt_tokens"], rec["completion_tokens"], rec["total_tokens"]) == (0, 0, 0)
    assert rec["cost_estimate"] == 0.0
    assert rec["success"] is True
    # 决策行零成本是**已知**事实，不得混进 unknown_cost_calls / estimated_calls
    assert rec["pricing"] == "known"
    assert rec["estimated"] is False
    stats = t.get_stats(days=7)
    assert stats["total_tokens"] == 0
    assert stats["total_cost"] == 0.0
    assert stats["unknown_cost_calls"] == 0
    assert stats["estimated_calls"] == 0


def _query(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return list(conn.execute(sql).fetchall())
    finally:
        conn.close()


def test_record_route_decision_normalizes_lane_and_keeps_two_values_only(tmp_path):
    t = UsageTracker(str(tmp_path / "ai_usage.db"))
    for lane in ("deep", "FAST", "", None, "shell", "kernel:dsh"):
        t.record_route_decision(lane)  # type: ignore[arg-type]
    t.close_all()
    counts = dict(_query(tmp_path / "ai_usage.db",
                         "SELECT source, COUNT(*) FROM usage_records GROUP BY source"))
    assert counts == {"route:fast": 1, "route:deep": 5}


def test_route_decision_rows_never_enter_kernel_sample(tmp_path):
    """D20 观察窗口径（source LIKE 'kernel:%'）不得被决策行抬高。"""
    db = tmp_path / "ai_usage.db"
    t = UsageTracker(str(db))
    for _ in range(5):
        t.record_route_decision("fast")
    t.record("deepseek", "deepseek-v4-flash", 10, 10, source="kernel:dsh")
    kernel = _query(db, "SELECT COUNT(*) FROM usage_records WHERE source LIKE 'kernel:%'")[0][0]
    route = _query(db, "SELECT COUNT(*) FROM usage_records WHERE source LIKE 'route:%'")[0][0]
    assert (kernel, route) == (1, 5)


# ── 任务一：写点（gateway_path 判定出口）───────────────────────


@pytest.mark.parametrize(
    ("params", "chat_route", "message", "expected_lane", "expected_bool"),
    [
        ({"kernel": "dsh"}, "legacy", None, "deep", True),        # L1 强制内核
        ({}, "gateway", None, "deep", True),                      # L2 显式网关
        ({}, "auto", "帮我在桌面创建一个txt文件", "deep", True),    # L3 复杂→深档
        ({}, "auto", "你好呀", "fast", False),                     # L3 简单→快档
        ({}, "auto", None, "fast", False),                         # L3 无 message 保守
        ({}, "legacy", None, "fast", False),                       # L4 缺省 legacy
    ],
)
def test_each_route_decision_point_records_one_lane(
    params, chat_route, message, expected_lane, expected_bool, monkeypatch
):
    """埋点必须落在判定出口：返回值语义不变 + 每次判定恰好一行。"""
    rec = _LaneRecorder()
    monkeypatch.setattr(ut, "get_usage_tracker", lambda: rec)
    got = _use_gateway_route(_ctx(chat_route), params, message)
    assert got is expected_bool
    assert rec.lanes == [expected_lane]


def test_route_telemetry_failure_cannot_break_routing(monkeypatch):
    class _Boom:
        def record_route_decision(self, lane: str) -> None:
            raise RuntimeError("db locked")

    monkeypatch.setattr(ut, "get_usage_tracker", lambda: _Boom())
    assert _use_gateway_route(_ctx("gateway"), {}) is True
    assert _use_gateway_route(_ctx("legacy"), {}) is False


def test_route_telemetry_skips_silently_without_tracker(monkeypatch):
    """tracker 未初始化（生产早期/单测）→ 静默跳过，且不影响判定。"""
    monkeypatch.setattr(ut, "get_usage_tracker", lambda: None)
    assert _use_gateway_route(_ctx("auto"), {}, "帮我在桌面创建一个txt文件") is True


def test_route_telemetry_tolerates_legacy_tracker_without_method(monkeypatch):
    """旧替身/旧 tracker 对象无 record_route_decision 时不得抛（getattr 兜底）。"""
    class _Legacy:
        def record(self, **kwargs):
            raise AssertionError("must not fall back to record()")

    monkeypatch.setattr(ut, "get_usage_tracker", lambda: _Legacy())
    assert _use_gateway_route(_ctx("legacy"), {}) is False


def test_gateway_path_records_decision_into_real_tracker(tmp_path, monkeypatch):
    """端到端写点：经真实 UsageTracker 落库（不新建表，走 record_* 通道）。"""
    t = UsageTracker(str(tmp_path / "ai_usage.db"))
    monkeypatch.setattr(ut, "get_usage_tracker", lambda: t)
    _use_gateway_route(_ctx("auto"), {}, "帮我在桌面创建一个txt文件")
    _use_gateway_route(_ctx("auto"), {}, "你好呀")
    rows = _query(
        tmp_path / "ai_usage.db",
        "SELECT source, provider, model, total_tokens, success FROM usage_records ORDER BY id",
    )
    assert [r[0] for r in rows] == ["route:deep", "route:fast"]
    assert all(r[1] == "router" and r[2] == "decision" and r[3] == 0 and r[4] == 1 for r in rows)


# ── 任务三：route_report 兼容回归（临时库注入）─────────────────


def _seed_mixed_db(path: Path) -> None:
    t = UsageTracker(str(path))
    for _ in range(3):
        t.record_route_decision("fast")
    t.record_route_decision("deep")
    for _ in range(2):
        t.record("deepseek", "deepseek-v4-flash", 100, 50, source="kernel:dsh")
    t.close_all()


def test_route_report_computes_fast_ratio_from_decision_rows(tmp_path, route_report):
    db = tmp_path / "ai_usage.db"
    _seed_mixed_db(db)
    m = route_report.compute_usage_metrics(str(db), days=1)
    assert m["route_telemetry_present"] is True
    assert m["fast_ratio"] == 0.75          # 3 fast / (3 fast + 1 deep)
    assert m["fast_ratio_source"] == "usage.source=route:*"
    # 关键不污染：kernel 样本仍只数 source=kernel:* 的两条
    assert m["kernel_calls"] == 2
    assert m["kernel_fail"] == 0
    assert m["kernel_fail_ratio"] == 0.0
    assert m["unknown_cost_calls"] == 0
    assert m["estimated_calls"] == 0
    assert [row["source"] for row in m["kernel_by_model"]] == ["kernel:dsh"]


def test_route_report_without_telemetry_still_returns_none(tmp_path, route_report):
    """埋点缺失时仍记 None（不得拿 shell 直调冒充路由占比）。"""
    db = tmp_path / "no_route.db"
    t = UsageTracker(str(db))
    t.record("deepseek", "deepseek-v4-flash", 10, 10, source="shell")
    t.close_all()
    m = route_report.compute_usage_metrics(str(db), days=1)
    assert m["fast_ratio"] is None
    assert m["route_telemetry_present"] is False


def test_window_verdict_ignores_decision_rows_and_drops_missing_telemetry_warning(
    tmp_path, route_report
):
    db = tmp_path / "ai_usage.db"
    _seed_mixed_db(db)
    usage = route_report.compute_usage_metrics(str(db), days=14)
    ttfb = {"min_span_days": None}
    win = route_report.evaluate_window(usage, ttfb)
    assert win["kernel_calls"] == 2                      # 未被 4 条决策行抬到 6
    assert win["route_telemetry_present"] is True
    assert win["fast_ratio"] == 0.75
    assert not any("缺路由埋点" in r for r in win["reasons"])
    # 决策行再多也不足以让观察窗误判为样本达标
    assert win["verdict"] == "样本不足"


def test_route_report_cli_json_emits_route_telemetry(tmp_path, capsys, route_report):
    db = tmp_path / "ai_usage.db"
    _seed_mixed_db(db)
    rc = route_report.main(
        ["--days", "1", "--format", "json", "--usage-db", str(db),
         "--trace-db", str(tmp_path / "absent.db")]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["usage"]["fast_ratio"] == 0.75
    assert payload["usage"]["fast_ratio_source"] == "usage.source=route:*"
    assert payload["usage"]["kernel_calls"] == 2


# ── 任务二：not_configured 分型漏判 ────────────────────────────


@pytest.mark.parametrize(
    ("exc", "expected_kind"),
    [
        # 回退链耗尽（非流式）：error_kind 是链内部标签，文本带耗尽文案 + 无 HTTP 状态
        (AIServiceError("AI 服务不可用，且未命中本地回退", error_kind="fallback_exhausted"),
         "not_configured"),
        # 回退链耗尽（流式）：ai.types.AIServiceError 的占位 error_kind="unknown_error"
        (AIServiceError("流式对话在所有提供方均失败"), "not_configured"),
        (AIServiceError("流式+工具调用在所有提供方均失败: boom"), "not_configured"),
        # 无 ai_client 的显式文案（既有 marker 已覆盖，锁定不回退）
        (RuntimeError("AI client not available — no provider configured"), "not_configured"),
        (RuntimeError("provider 未配置"), "not_configured"),
    ],
)
def test_not_configured_grading(exc, expected_kind):
    payload = grade_stream_exception(exc)
    assert payload["error_kind"] == expected_kind
    assert "尚未配置" in payload["friendly"]
    assert payload["transient"] is False


def test_exhaustion_with_http_status_is_not_masked_as_not_configured():
    """欠费/鉴权导致的链耗尽必须保留真因，不得被耗尽文案升格为「未配置」。"""
    billing = grade_stream_exception(
        AIServiceError("AI 服务不可用，且未命中本地回退", status=402, error_kind="fallback_exhausted")
    )
    assert billing["error_kind"] == "billing_error"
    auth = grade_stream_exception(
        AIServiceError("AI 服务不可用，且未命中本地回退", status=401, error_kind="fallback_exhausted")
    )
    assert auth["error_kind"] == "auth_error"


def test_placeholder_kind_does_not_override_explicit_provider_kind():
    """provider 给出有效分型时一律采信（占位值只补判未配置，不重算其它类）。"""
    payload = grade_stream_exception(
        AIServiceError("流式对话在所有提供方均失败", status=429, error_kind="rate_limit")
    )
    assert payload["error_kind"] == "rate_limit"
    assert payload["status"] == 429
    # transient 取异常自带属性（provider 未声明 → AIServiceError 默认 False），
    # 本任务不改写该口径
    assert payload["transient"] is False
    # 无耗尽文案的占位 unknown_error 保持原样，不凭空判成「未配置」
    plain = grade_stream_exception(AIServiceError("something odd"))
    assert plain["error_kind"] == "unknown_error"
    assert "生成失败" in plain["friendly"]
