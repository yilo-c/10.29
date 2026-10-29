# -*- coding: utf-8 -*-
"""C-38 记账修复：内核用量真实 provider/model 解析 + estimated 标记 + unknown 不回落。

覆盖（对应任务一三处失真）：
- ①provider/model 不再硬编码 "kernel"/name：
  · 壳侧 model_patch（provider/model）优先；
  · dsh 无注入时读 ~/.dsh/settings.yaml agent-default-model（配置真值）；
  · AutoGateway 下钻 active 候选；
  · 拿不到真值 → provider='kernel:<name>'，不编造具体付费模型。
- ②token：内核无真实 usage → 字符估算且 estimated=1；result.usage 提供
  prompt/completion → 精确值 estimated=0；usage 形状不认识时不得把
  {"tokens": 1} 这类聚合数当 prompt=1。
- ③成本：estimate_cost 未知模型返回 None；record() 落 pricing='unknown'、
  cost 占位 0.0；get_stats 暴露 unknown_cost_calls/estimated_calls。
"""
from __future__ import annotations

import sqlite3

from ai.usage_tracker import (
    PRICING_TABLE,
    UsageTracker,
    estimate_cost,
    init_usage_tracker,
    reset_usage_tracker_for_tests,
)
from backend.handlers.chat_paths import gateway_path as gp
from backend.handlers.chat_paths.gateway_path import (
    _extract_kernel_usage,
    _read_dsh_default_model,
    _record_kernel_usage,
    _resolve_kernel_provider_model,
)
from services.agent_gateway.protocol import AgentRunEvent


class _Gw:
    """最小网关替身：只带解析所需属性。"""

    name = "dsh"

    def __init__(self, model_patch: str = "", **extra):
        self.model_patch = model_patch
        for k, v in extra.items():
            setattr(self, k, v)


# ── ① 真实 provider/model 解析 ─────────────────────────────────


def test_model_patch_is_authoritative_and_not_hardcoded():
    gw = _Gw(model_patch="volc/deepseek-v4-flash")
    provider, model, tag = _resolve_kernel_provider_model(gw, "dsh")
    assert (provider, model, tag) == ("volc", "deepseek-v4-flash", "model_patch")
    # 绝非旧的硬编码 ("kernel", "dsh")
    assert provider != "kernel"


def test_dsh_settings_default_model_is_used_when_no_patch(monkeypatch):
    calls: list[int] = []

    def fake_reader():
        calls.append(1)
        return ("bailian-qwen", "qwen3.8-flash")  # 2026-09-12 实测 ~/.dsh/settings.yaml 真值

    monkeypatch.setattr(gp, "_read_dsh_default_model", fake_reader)
    provider, model, tag = _resolve_kernel_provider_model(_Gw(), "dsh")
    assert (provider, model, tag) == ("bailian-qwen", "qwen3.8-flash", "dsh_settings")
    assert calls == [1]


def test_auto_gateway_drills_into_active_candidate(monkeypatch):
    monkeypatch.setattr(gp, "_read_dsh_default_model", lambda: ("bailian-qwen", "qwen3.8-flash"))
    auto = _Gw(_active="dsh", _pool={"dsh": _Gw()})
    provider, model, tag = _resolve_kernel_provider_model(auto, "dsh")
    assert (provider, model, tag) == ("bailian-qwen", "qwen3.8-flash", "dsh_settings")


def test_unknown_fallback_does_not_fabricate_model():
    # 拿不到真值：provider 记 kernel:<name>、model 记 name，不编造付费模型
    provider, model, tag = _resolve_kernel_provider_model(_Gw(), "mystery")
    assert (provider, model, tag) == ("kernel:mystery", "mystery", "unknown")


def test_read_dsh_default_model_parses_real_block_format(tmp_path, monkeypatch):
    # 现场 settings.yaml 含畸形键（bailian:ilian:）会破坏严格 YAML 解析，
    # 轻量扫描必须照样提取 agent-default-model；同时畸形缩进 → None 而非猜值。
    fake = tmp_path / "settings.yaml"
    fake.write_text(
        "llm-pi-ai:\n"
        "  providers:\n"
        "    bailian:ilian:\n"
        "      models:\n"
        "        - id: qwen-image-3.0-pro\n"
        "agent-default-model:\n"
        "  provider: bailian-qwen\n"
        "  model: qwen3.8-flash\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gp.os.path, "expanduser", lambda _p: str(fake))
    assert _read_dsh_default_model() == ("bailian-qwen", "qwen3.8-flash")

    missing = tmp_path / "none.yaml"
    monkeypatch.setattr(gp.os.path, "expanduser", lambda _p: str(missing))
    assert _read_dsh_default_model() is None


# ── ③ 成本 unknown 不回落 ──────────────────────────────────────


def test_estimate_cost_unknown_returns_none():
    assert estimate_cost("qwen3.8-flash", 10_000, 10_000) is None
    assert estimate_cost("dsh", 10_000, 10_000) is None


def test_estimate_cost_known_model_still_works():
    # deepseek-v4-flash 单价行已在 PRICING_TABLE（prompt 0.06/M → 1e-4 per 1K 口径）
    assert estimate_cost("deepseek-v4-flash", 1000, 1000) == round(
        1000 * PRICING_TABLE["deepseek-v4-flash"]["prompt"] / 1000
        + 1000 * PRICING_TABLE["deepseek-v4-flash"]["completion"] / 1000,
        6,
    )
    # 显式零价的本地模型 ≠ unknown（区分"已知免费"与"查不到"）
    assert estimate_cost("qwen3-4b", 1000, 1000) == 0.0


def test_record_marks_unknown_pricing_without_default_fallback(tmp_path):
    t = UsageTracker(str(tmp_path / "u.db"))
    t.record("bailian-qwen", "qwen3.8-flash", 5000, 8000, estimated=True)
    rec = t.get_records(limit=5)[0]
    assert rec["pricing"] == "unknown"
    assert rec["cost_estimate"] == 0.0  # 占位，不是"假装有数"
    assert rec["estimated"] is True
    stats = t.get_stats(days=7)
    assert stats["unknown_cost_calls"] == 1
    assert stats["estimated_calls"] == 1
    assert stats["total_cost"] == 0.0
    # 已知定价的正常行不受影响
    t.record("deepseek", "deepseek-v4-flash", 1000, 1000)
    stats = t.get_stats(days=7)
    assert stats["unknown_cost_calls"] == 1  # 只有内核那条 unknown
    assert stats["estimated_calls"] == 1     # 正常调用不算估算
    assert stats["total_cost"] > 0.0


def test_legacy_schema_gets_columns_added(tmp_path):
    # 老库（无 source/estimated/pricing 列）：构造 tracker 自动补列，record 正常
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE usage_records (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " timestamp TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,"
        " prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,"
        " total_tokens INTEGER NOT NULL DEFAULT 0, cost_estimate REAL NOT NULL DEFAULT 0.0,"
        " success INTEGER NOT NULL DEFAULT 1)"
    )
    conn.commit()
    conn.close()
    t = UsageTracker(db)
    t.record("kernel:x", "x", 10, 10, estimated=True)
    rec = t.get_records(limit=5)[0]
    assert rec["estimated"] is True
    assert rec["pricing"] in ("known", "unknown")


# ── ② estimated 标记（经 _record_kernel_usage 全链路）──────────


def _fresh_tracker(tmp_path):
    reset_usage_tracker_for_tests()
    return init_usage_tracker(str(tmp_path / "ai_usage.db"))


def test_record_kernel_usage_estimated_flag_and_real_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "_read_dsh_default_model", lambda: ("bailian-qwen", "qwen3.8-flash"))
    t = _fresh_tracker(tmp_path)
    try:
        gw = _Gw(model_patch="volc/deepseek-v4-flash")
        _record_kernel_usage("dsh", None, "你好", "回复内容", True, gw=gw, events=[])
        rec = t.get_records(limit=5)[0]
        assert rec["provider"] == "volc"
        assert rec["model"] == "deepseek-v4-flash"
        assert rec["source"] == "kernel:dsh"
        assert rec["estimated"] is True            # 无真实 usage → 字符估算
        assert rec["pricing"] == "known"           # 该模型有单价行
        assert rec["cost_estimate"] > 0.0
    finally:
        reset_usage_tracker_for_tests()


def test_record_kernel_usage_real_usage_sets_estimated_false(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "_read_dsh_default_model", lambda: None)
    t = _fresh_tracker(tmp_path)
    try:
        events = [
            AgentRunEvent(kind="result", payload={"text": "ok", "usage": {"prompt_tokens": 10, "completion_tokens": 20}})
        ]
        _record_kernel_usage("dsh", None, "x" * 400, "y" * 400, True, gw=_Gw(), events=events)
        rec = t.get_records(limit=5)[0]
        assert rec["estimated"] is False
        assert rec["prompt_tokens"] == 10          # 真实值覆盖字符估算
        assert rec["completion_tokens"] == 20
    finally:
        reset_usage_tracker_for_tests()


def test_extract_kernel_usage_rejects_unrecognized_usage_shape():
    # 内核真实返回里只有聚合 {"tokens": 1} → 认不出 prompt/completion → None
    # （绝不把 1 当 prompt_tokens 记成精确值；这正是 FakeKernelGateway 的场景）
    events = [AgentRunEvent(kind="result", payload={"text": "ab", "usage": {"tokens": 1}})]
    assert _extract_kernel_usage(events) is None
    # usage=None（DSH 现状）→ None
    assert _extract_kernel_usage([AgentRunEvent(kind="result", payload={"usage": None})]) is None
    # 认识的键族：openai 风格与 anthropic 风格都要能取到
    assert _extract_kernel_usage([AgentRunEvent(kind="result", payload={"usage": {"input_tokens": 3, "output_tokens": 4}})]) == (3, 4)
