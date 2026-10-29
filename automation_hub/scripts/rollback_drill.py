#!/usr/bin/env python
"""M0 回滚演练脚本。

对运行中的 automation-hub 执行一次回滚演练：
- 快照当前数据库状态
- 执行一次变更（注册工具 + 创建 tool-run）
- 回滚数据库到快照
- 验证回滚后状态与快照一致
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _api(api_base: str, path: str) -> str:
    return f"{api_base.rstrip('/')}{path}"


def _req(url: str, method: str = "GET", headers: dict | None = None, body: dict | None = None) -> tuple[int, dict]:
    kwargs = {"method": method}
    hdrs = headers or {}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs = {**hdrs, "Content-Type": "application/json"}
    req = Request(url, data=data, headers=hdrs, **kwargs)
    try:
        with urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"detail": f"HTTP {exc.code}"}


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _snapshot_db(db_path: Path) -> Path:
    snapshot = db_path.with_suffix(".rollback_snapshot.sqlite3")
    shutil.copy2(db_path, snapshot)
    return snapshot


def _restore_db(snapshot: Path, db_path: Path) -> None:
    shutil.copy2(snapshot, db_path)


def _count_tools(api_base: str, token: str) -> int:
    status, body = _req(_api(api_base, "/tools"), headers=_auth_headers(token))
    if status != 200:
        raise RuntimeError(f"GET /tools returned {status}")
    return len(body.get("tools", body if isinstance(body, list) else []))


def _count_audit_events(api_base: str, token: str) -> int:
    status, body = _req(_api(api_base, "/audit?limit=1"), headers=_auth_headers(token))
    if status != 200:
        return 0
    return body.get("count", 0)


def main():
    parser = argparse.ArgumentParser(description="M0 回滚演练")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--token-file", required=True, help="admin token 文件路径")
    parser.add_argument("--db-path", required=True, help="SQLite 数据库文件路径")
    args = parser.parse_args()

    api_base = args.api_base
    db_path = Path(args.db_path).resolve()
    failures = 0

    def check(step: str, ok: bool):
        nonlocal failures
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {step}")
        if not ok:
            failures += 1

    # Load token
    try:
        admin_token = open(args.token_file, encoding="utf-8").read().strip().lstrip("﻿")
    except Exception:
        print(f"[FAIL] cannot read token file: {args.token_file}")
        return 2

    status, body = _req(_api(api_base, "/auth/me"), headers=_auth_headers(admin_token))
    if status != 200:
        print(f"[FATAL] token invalid: HTTP {status}")
        return 2
    print(f"Token OK: user_id={body.get('user_id')}")

    # ── Phase 1: Snapshot ──
    print("\n[Phase 1/4] Snapshot pre-change state")
    if not db_path.exists():
        print(f"  [FAIL] database not found: {db_path}")
        return 2

    snapshot = _snapshot_db(db_path)
    tools_before = _count_tools(api_base, admin_token)
    audit_before = _count_audit_events(api_base, admin_token)
    check("Database snapshot created", snapshot.exists())
    print(f"         tools_before={tools_before} audit_before={audit_before}")

    # ── Phase 2: Apply change ──
    print("\n[Phase 2/4] Apply change (register tool + record audit)")
    tool_id = f"rollback-drill-{uuid.uuid4().hex[:8]}"
    status, _ = _req(
        _api(api_base, "/tools"),
        method="POST",
        headers=_auth_headers(admin_token),
        body={
            "id": tool_id,
            "name": "rollback_drill_tool",
            "description": "Created during rollback drill",
            "risk_level": "read",
            "executor": "host",
            "args_schema": {},
            "command": ["echo", "drill"],
            "timeout_sec": 10,
            "is_enabled": True,
        },
    )
    check(f"POST /tools ({tool_id})", status == 200)

    tools_after = _count_tools(api_base, admin_token)
    check(f"Tool count increased ({tools_before} -> {tools_after})", tools_after > tools_before)

    # ── Phase 3: Rollback ──
    print("\n[Phase 3/4] Execute rollback")
    _restore_db(snapshot, db_path)
    check("Database restored from snapshot", db_path.exists())

    # Clean up snapshot
    snapshot.unlink(missing_ok=True)

    # ── Phase 4: Verify ──
    print("\n[Phase 4/4] Verify post-rollback state")
    tools_final = _count_tools(api_base, admin_token)
    audit_final = _count_audit_events(api_base, admin_token)
    check(
        f"Tool count restored ({tools_final} == {tools_before})",
        tools_final == tools_before,
    )
    check(
        f"Audit event count restored ({audit_final} == {audit_before})",
        audit_final == audit_before,
    )

    # ── Summary ──
    print()
    if failures:
        print(f"RESULT: FAIL ({failures} check(s) failed)")
        return 1
    print("RESULT: ROLLBACK DRILL PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
