# -*- coding: utf-8 -*-
"""V2-02 回归测试：usage.record -> behavior_logger 接线。

背景（2026-08-24）：前端行为上报（usage.record）此前只写 usage_stats.json，
未落入行为日志，前端采集盲区。本测试钉死两条契约：
  1. record_action 必须向 context.behavior_logger.record 投递一条 BehaviorEvent
  2. 事件隐私分级必须为 local（C5：仅本地、不上云）
若接线被移除或 privacy 被改成 synced，立即红。
"""
from __future__ import annotations

import json

import pytest

from services.usage_stats import record_action


class _FakeBehaviorLogger:
    def __init__(self) -> None:
        self.events: list = []

    def record(self, event) -> None:
        self.events.append(event)


class _FakeContext:
    def __init__(self, behavior_logger=None, base_dir=None):
        self.behavior_logger = behavior_logger
        self.local_data_store = type("DS", (), {"base_dir": base_dir or "/tmp/none"})()
        self._stats_dir = base_dir


def test_record_action_emits_local_behavior_event(tmp_path):
    bl = _FakeBehaviorLogger()
    ctx = _FakeContext(behavior_logger=bl, base_dir=str(tmp_path))

    record_action(ctx, "module.open", domain=None)

    assert len(bl.events) == 1
    ev = bl.events[0]
    # 隐私分级必须 local（C5：不上云）
    assert ev.privacy == "local"
    assert ev.action == "module.open"
    assert ev.category == "app"
    assert ev.source == "frontend"


def test_record_action_passes_domain_to_detail(tmp_path):
    bl = _FakeBehaviorLogger()
    ctx = _FakeContext(behavior_logger=bl, base_dir=str(tmp_path))

    record_action(ctx, "bookmark", domain="github.com")

    assert len(bl.events) == 1
    assert bl.events[0].detail.get("domain") == "github.com"


def test_record_action_without_behavior_logger_is_graceful(tmp_path):
    # 后端未注入 behavior_logger（headless 未启用采集）时不应抛异常
    ctx = _FakeContext(behavior_logger=None, base_dir=str(tmp_path))

    record_action(ctx, "capture.note")

    # 用量统计仍应正常写入
    path = tmp_path / "usage_stats.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "capture.note" in data["actions"]
