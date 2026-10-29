"""AuditWriter 单元测试：独立 SQLite 单写线程的审计写入器。

覆盖：intent→status 生命周期、条件查询、pending 可见性、集成日志、同目录单例。
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from core.audit_writer import AuditWriter, get_audit_writer


def _intent(entry_id: str, action_id: str = "notes.update", target_id: str = "1") -> dict:
    return {
        "entry_id": entry_id,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "actor": "user",
        "action_id": action_id,
        "target_type": "note",
        "target_id": target_id,
        "operation": "update",
        "before_snapshot": {"v": 1},
        "after_snapshot": None,
        "status": "intent",
        "metadata": {},
    }


@pytest.fixture
def writer(tmp_path):
    w = AuditWriter(str(tmp_path))
    yield w
    w.shutdown()


def test_submit_intent_then_status(writer):
    writer.submit_intent(_intent("e-1"))
    writer.submit_status("e-1", "completed", after_snapshot={"v": 2})
    writer.drain()

    entry = writer.fetch_entry("e-1")
    assert entry is not None
    assert entry["status"] == "completed"
    assert entry["after_snapshot"] == {"v": 2}
    assert entry["before_snapshot"] == {"v": 1}
    assert entry["actor"] == "user"


def test_fetch_entries_filters(writer):
    for i in range(3):
        writer.submit_intent(_intent(f"e-{i}", action_id=f"a.{i}", target_id=str(i)))
    writer.drain()

    all_entries = writer.fetch_entries(limit=100)
    assert len(all_entries) == 3

    a0 = writer.fetch_entries(action_id="a.0", limit=10)
    assert len(a0) == 1 and a0[0]["entry_id"] == "e-0"


def test_fetch_entry_nonexistent(writer):
    assert writer.fetch_entry("ghost") is None


def test_pending_visibility_before_flush(writer):
    writer.submit_intent(_intent("pe-1"))
    # 不 drain：写入仍在队列中，但读必须立即可见（pending 合并）
    assert writer.fetch_entry("pe-1") is not None
    assert "pe-1" in writer.pending_entries() or writer.fetch_entry("pe-1") is not None


def test_submit_integration_and_query(writer):
    writer.submit_integration("youdao", "oauth.upsert", "ok", {"has_token": True})
    writer.drain()

    logs = writer.fetch_integration(provider="youdao")
    assert len(logs) == 1
    assert logs[0]["event"] == "oauth.upsert"
    assert logs[0]["payload"] == {"has_token": True}
    assert logs[0]["provider"] == "youdao"


def test_submit_integration_provider_filter(writer):
    writer.submit_integration("youdao", "oauth.upsert", "ok", {})
    writer.submit_integration("feishu", "oauth.upsert", "ok", {})
    writer.drain()

    youdao_logs = writer.fetch_integration(provider="youdao")
    assert len(youdao_logs) == 1
    all_logs = writer.fetch_integration()
    assert len(all_logs) == 2


def test_singleton_same_data_dir(tmp_path):
    a = get_audit_writer(data_dir=str(tmp_path))
    b = get_audit_writer(data_dir=str(tmp_path))
    assert a is b
    a.shutdown()
