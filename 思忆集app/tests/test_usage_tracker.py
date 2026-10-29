# -*- coding: utf-8 -*-
"""A 项观察期埋点：UsageTracker source 字段契约。

覆盖：
- source 默认 'shell'：record() 不传 source 时默认 'shell'。
- by_source 聚合：不同 source 的记录正确分组聚合（tokens/cost/calls）。
- ALTER 兜底：既有库缺 source 列时 record() 自动 ALTER 补齐后写入（零迁移兜底）。
- get_records 透出 source：detail 视图含 source 字段。
"""
from __future__ import annotations

import sqlite3

from ai.usage_tracker import UsageTracker


def test_source_defaults_to_shell(tmp_path):
    t = UsageTracker(str(tmp_path / "u.db"))
    t.record("deepseek", "deepseek-chat", 10, 20)
    recs = t.get_records(limit=5)
    assert recs and recs[0]["source"] == "shell"


def test_by_source_aggregation(tmp_path):
    t = UsageTracker(str(tmp_path / "u.db"))
    t.record("deepseek", "deepseek-chat", 100, 200, source="shell")
    t.record("kernel", "dsh", 300, 400, source="kernel:dsh")
    t.record("kernel", "dsh", 30, 40, source="kernel:dsh")
    t.record("kernel", "claude_code", 5, 5, source="kernel:claude_code")
    stats = t.get_stats(days=7)
    by_source = {r["source"]: r for r in stats["by_source"]}
    assert by_source["shell"]["calls"] == 1
    assert by_source["shell"]["tokens"] == 300
    assert by_source["kernel:dsh"]["calls"] == 2
    assert by_source["kernel:dsh"]["tokens"] == 770  # 700 + 70
    assert by_source["kernel:claude_code"]["calls"] == 1
    assert by_source["kernel:claude_code"]["tokens"] == 10
    # 总调用数 = 各 source 之和
    assert stats["total_calls"] == 4


def test_alter_fallback_adds_source_column(tmp_path):
    """既有旧库（无 source 列）：record() 走 ALTER 兜底补齐后写入（零迁移）。"""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE usage_records ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,"
        " provider TEXT NOT NULL, model TEXT NOT NULL,"
        " prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,"
        " total_tokens INTEGER NOT NULL DEFAULT 0, cost_estimate REAL NOT NULL DEFAULT 0.0,"
        " success INTEGER NOT NULL DEFAULT 1)"
    )
    conn.commit()
    conn.close()

    t = UsageTracker(db)
    # 模拟"极端缺列"状态：重建无 source 列的表，逼 record() 内部 ALTER 兜底
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE usage_records_tmp AS SELECT "
        "id, timestamp, provider, model, prompt_tokens, completion_tokens, "
        "total_tokens, cost_estimate, success FROM usage_records"
    )
    conn.execute("DROP TABLE usage_records")
    conn.execute("ALTER TABLE usage_records_tmp RENAME TO usage_records")
    conn.commit()
    conn.close()

    t.record("deepseek", "deepseek-chat", 1, 1)  # 触发 record() 内部 ALTER 兜底
    recs = t.get_records(limit=5)
    assert recs and recs[0]["source"] == "shell"


def test_get_records_exposes_source(tmp_path):
    t = UsageTracker(str(tmp_path / "u.db"))
    t.record("kernel", "dsh", 1, 2, source="kernel:dsh")
    t.record("kernel", "codex", 3, 4, source="kernel:codex")
    recs = t.get_records(limit=10)
    assert len(recs) == 2
    assert all("source" in r for r in recs)
    assert {r["source"] for r in recs} == {"kernel:dsh", "kernel:codex"}
