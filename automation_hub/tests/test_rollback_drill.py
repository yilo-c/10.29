from __future__ import annotations

import shutil

from sqlalchemy import text

from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client, register_tool


def test_rollback_drill_snapshot_apply_restore_verify(tmp_path, monkeypatch):
    """M0-WP4 回滚演练：快照 → 变更 → 回滚 → 验证。"""
    env = configure_test_env(tmp_path, monkeypatch)
    db_path = env["database_path"]

    client = make_client()
    headers = bootstrap_headers(client)

    # ── Phase 1: Snapshot ──
    # WAL 模式：先把 WAL 全部 checkpoint 进主库，保证单文件快照完整
    from api.db.session import _reset_session_factory, get_db_session
    with get_db_session() as session:
        session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    snapshot_path = db_path.with_suffix(".drill_snapshot.sqlite3")
    shutil.copy2(db_path, snapshot_path)
    assert snapshot_path.exists()

    # Record pre-change stats
    tools_response = client.get("/tools", headers=headers)
    assert tools_response.status_code == 200, tools_response.text
    tools_before = len(tools_response.json().get("tools", []))

    audit_response = client.get("/audit?limit=1", headers=headers)
    audit_before_count = audit_response.json().get("count", 0) if audit_response.status_code == 200 else 0

    # ── Phase 2: Apply change ──
    register_tool(client, headers, "drill-tool", "read", command=["echo", "drill"])

    tools_response = client.get("/tools", headers=headers)
    tools_after = len(tools_response.json().get("tools", []))
    assert tools_after > tools_before

    # ── Phase 3: Rollback ──
    # 先 dispose 引擎释放文件句柄，再还原主库并删除 WAL/SHM 侧车文件，
    # 否则旧 WAL 中已提交的变更会被新连接重放，回滚不生效。
    _reset_session_factory()
    shutil.copy2(snapshot_path, db_path)
    for sidecar in (db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
        sidecar.unlink(missing_ok=True)
    _reset_session_factory()
    snapshot_path.unlink(missing_ok=True)

    # ── Phase 4: Verify ──
    tools_response = client.get("/tools", headers=headers)
    assert tools_response.status_code == 200, tools_response.text
    tools_final = len(tools_response.json().get("tools", []))
    assert tools_final == tools_before, f"Expected {tools_before} tools after rollback, got {tools_final}"

    audit_response = client.get("/audit?limit=1", headers=headers)
    audit_final_count = audit_response.json().get("count", 0) if audit_response.status_code == 200 else 0
    assert audit_final_count == audit_before_count, f"Expected {audit_before_count} audit events, got {audit_final_count}"
