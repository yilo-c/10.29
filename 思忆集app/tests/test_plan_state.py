# -*- coding: utf-8 -*-
"""B-P1a.1 plan_state 单测（纯逻辑、注入时间戳、零 IO）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.plan_state import Plan  # noqa: E402


def test_add_start_complete_lifecycle():
    p = Plan(goal="整理本周资料", max_steps=8)
    s1 = p.add_step("检索相关笔记")
    s2 = p.add_step("生成摘要")
    assert s1 and s2 and s1.id != s2.id
    assert p.total == 2 and p.next_pending().id == s1.id

    assert p.start(s1.id, now=1.0) is True
    assert p.get(s1.id).status == "running" and p.get(s1.id).started_at == 1.0
    assert p.complete(s1.id, now=2.0, detail="命中 5 条") is True
    assert p.done_count == 1 and p.get(s1.id).detail == "命中 5 条"
    # 已终态不可再转移
    assert p.start(s1.id, now=3.0) is False
    assert p.complete(s1.id, now=3.0) is False


def test_budget_cap_blocks_over_limit():
    p = Plan(goal="g", max_steps=3)
    assert [p.add_step(f"s{i}") is not None for i in range(3)] == [True, True, True]
    assert p.add_step("超预算的第4步") is None      # 第 4 步被拒
    assert p.total == 3 and p.remaining_steps() == 0


def test_fail_and_skip_and_has_failure():
    p = Plan(goal="g", max_steps=5)
    a = p.add_step("a"); b = p.add_step("b"); c = p.add_step("c")
    p.start(a.id, 1.0); p.fail(a.id, 2.0, reason="工具报错")
    p.skip(b.id, 2.5, reason="不需要")
    assert p.has_failure is True
    assert p.get(a.id).status == "failed" and p.get(b.id).status == "skipped"
    assert not p.all_terminal
    p.start(c.id, 3.0); p.complete(c.id, 4.0)
    assert p.all_terminal is True


def test_running_and_next_tracking():
    p = Plan(goal="g")
    a = p.add_step("a"); b = p.add_step("b")
    p.start(a.id, 1.0)
    assert [s.id for s in p.running()] == [a.id]
    assert p.next_pending().id == b.id   # a 已 running，next 是 b


def test_event_payload_shape():
    p = Plan(goal="目标X", max_steps=8)
    a = p.add_step("步骤一")
    p.start(a.id, 1.0); p.complete(a.id, 2.0)
    p.add_step("步骤二")
    ev = p.to_event_payload()
    assert ev["kind"] == "plan" and ev["goal"] == "目标X"
    assert ev["done"] == 1 and ev["total"] == 2
    assert {s["status"] for s in ev["steps"]} == {"done", "pending"}
    assert ev["next"] == "s2" and ev["remaining_steps"] == 6
