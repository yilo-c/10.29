"""D22-4 迁移脚本合成数据自测（temp 目录，不触真实数据/生产库）。

覆盖：建表→迁移→全量比对 PASS；重跑幂等；无 url 条目可迁移（id 幂等键勘正）；
溢流字段经 raw_json 零丢失；--rollback 回灌后 kv 深比较相等；--snapshot-check。
真实数据 staging 演练属 🔒 人工批准项，不在本测试内。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "migrate_kv_to_tables.py")


def _info_items() -> list[dict]:
    return [
        {"id": 1, "url": "https://a.example/1", "title": "A", "summary": "sa",
         "summary_zh": "中文摘要", "category": "dev", "source": "rss-a",
         "tags": ["t1"], "read": False, "saved": True, "score": 8.5,
         "title_zh": "甲", "source_label": "RSS A", "notes": "n1",
         "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-02T00:00:00+00:00",
         "collected_at": "2026-01-01T00:00:00+00:00"},
        {"id": 2, "title": "无url条目", "source": "hackernews", "read": True, "saved": False,
         "tags": [], "created_at": "2026-02-01T00:00:00+00:00",
         "collected_at": "2026-02-01T00:00:00+00:00"},
        {"id": 3, "url": "https://c.example/3", "title": "C", "category": "ai",
         "source": "rss-b", "read": False, "saved": False,
         "tags": [], "created_at": "2026-03-01T00:00:00+00:00",
         "collected_at": "2026-03-01T00:00:00+00:00"},
    ]


def _inbox_entries() -> list[dict]:
    return [
        {"id": 10, "title": "inbox-1", "content": "body", "source": "manual",
         "status": "new", "tags": ["x"], "metadata": {"classification": {"k": "v"}},
         "kind": "note", "created_at": "2026-04-01T00:00:00+00:00",
         "updated_at": "2026-04-01T00:00:00+00:00"},
        {"id": 11, "title": "inbox-2", "content": "", "source": "relay",
         "status": "archived", "tags": [], "metadata": {},
         "created_at": "2026-04-02T00:00:00+00:00",
         "updated_at": "2026-04-03T00:00:00+00:00"},
    ]


@pytest.fixture()
def data_dir() -> str:
    d = tempfile.mkdtemp()
    from data.local_storage import LocalDataStore
    store = LocalDataStore(base_dir=d)
    store.save_json("info_items", _info_items())
    store.save_json("inbox_entries", _inbox_entries())
    return d


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True, text=True, encoding="utf-8",
    )


def _kv_array(d: str, key: str) -> list:
    import sqlite3
    conn = sqlite3.connect(os.path.join(d, "local_storage.db"))
    try:
        row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def test_migrate_verify_pass_and_idempotent(data_dir):
    r = _run(["--data-dir", data_dir])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout
    r2 = _run(["--data-dir", data_dir])
    assert r2.returncode == 0, "重跑应幂等"


def test_migrate_preserves_overflow_fields(data_dir):
    _run(["--data-dir", data_dir])
    import sqlite3
    conn = sqlite3.connect(os.path.join(data_dir, "local_storage.db"))
    try:
        rows = dict(conn.execute("SELECT id, raw_json FROM info_items_tb"))
        assert rows[1] is not None
        assert json.loads(rows[1])["notes"] == "n1"
        assert json.loads(rows[1])["title_zh"] == "甲"
        assert json.loads(rows[2])["title"] == "无url条目"
        ib = dict(conn.execute("SELECT entry_id, raw_json FROM inbox_entries_tb"))
        assert json.loads(ib[10])["metadata"]["classification"] == {"k": "v"}
    finally:
        conn.close()


def test_rollback_restores_kv_deep_equal(data_dir):
    before_info = _kv_array(data_dir, "info_items")
    before_inbox = _kv_array(data_dir, "inbox_entries")
    assert _run(["--data-dir", data_dir]).returncode == 0
    r = _run(["--data-dir", data_dir, "--rollback"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout
    assert sorted(_kv_array(data_dir, "info_items"), key=lambda x: x["id"]) == sorted(before_info, key=lambda x: x["id"])
    assert sorted(_kv_array(data_dir, "inbox_entries"), key=lambda x: x["id"]) == sorted(before_inbox, key=lambda x: x["id"])


def test_snapshot_check(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.db").write_bytes(b"x" * 100)
    (src / "b.json").write_text("hello", encoding="utf-8")
    r = _run(["--snapshot-check", str(src), str(dst)])
    assert r.returncode == 1
    for name in ("a.db", "b.json"):
        (dst / name).write_bytes((src / name).read_bytes())
    r2 = _run(["--snapshot-check", str(src), str(dst)])
    assert r2.returncode == 0, r2.stdout
