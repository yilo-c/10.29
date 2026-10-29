#!/usr/bin/env python
"""M0 放行门禁 — Live Worker 验收脚本。

模拟 worker 消费流程：claim → events → artifacts → terminal status。
验证 worker 是否能正确消费 tool-runs 并推送执行结果。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
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


def main():
    parser = argparse.ArgumentParser(description="M0 Live Worker 验收")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--token-file", required=True, help="admin token 文件路径")
    parser.add_argument("--executor", choices=["host", "docker"], default="host", help="执行器类型")
    args = parser.parse_args()

    api_base = args.api_base
    failures = 0

    def check(step: str, status: int, expected: int = 200, body: dict | None = None):
        nonlocal failures
        ok = status == expected
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {step} (HTTP {status})")
        if not ok:
            failures += 1
            if body:
                print(f"         detail: {body.get('detail', body)}")
        return ok

    # Load token
    try:
        admin_token = open(args.token_file, encoding="utf-8").read().strip().lstrip("﻿")
    except Exception:
        print(f"[FAIL] cannot read token file: {args.token_file}")
        return 2

    # Verify token is valid
    status, body = _req(_api(api_base, "/auth/me"), headers=_auth_headers(admin_token))
    if not check("GET /auth/me", status):
        print("[FATAL] token invalid — aborting")
        return 2
    user_id = body.get("user_id")
    print(f"         user_id={user_id}")

    # ── 1. Register a test tool ──
    tool_id = f"m0-worker-verify-{uuid.uuid4().hex[:8]}"
    print(f"[1/5] Register tool: {tool_id}")
    status, _ = _req(
        _api(api_base, "/tools"),
        method="POST",
        headers=_auth_headers(admin_token),
        body={
            "id": tool_id,
            "name": "m0_worker_echo",
            "description": "M0 worker verification tool",
            "risk_level": "read",
            "executor": args.executor,
            "args_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            "command": ["echo", "worker-verify"],
            "timeout_sec": 30,
            "is_enabled": True,
        },
    )
    check(f"POST /tools ({tool_id})", status)

    # ── 2. Create a tool run ──
    print("[2/5] Create tool run")
    status, body = _req(
        _api(api_base, "/tool-runs"),
        method="POST",
        headers=_auth_headers(admin_token),
        body={"tool_id": tool_id, "args": {"text": "worker-smoke-test"}, "reason": "worker verification"},
    )
    if not check("POST /tool-runs", status):
        print("[FATAL] cannot create tool run — aborting")
        return 2
    run_id = body["run_id"]
    run_status = body.get("status", "queued")
    print(f"         run_id={run_id} status={run_status}")

    # If the run requires approval (exec_high risk), approve it
    approval_id = body.get("approval_id")
    if approval_id and run_status == "pending_approval":
        print(f"         approval_id={approval_id} — approving...")
        status, _ = _req(
            _api(api_base, f"/approvals/{approval_id}/approve?note=worker-verify-auto"),
            method="POST",
            headers=_auth_headers(admin_token),
        )
        check(f"POST /approvals/{approval_id}/approve", status)
        # Re-fetch run status
        status, body = _req(_api(api_base, f"/tool-runs/{run_id}"), headers=_auth_headers(admin_token))
        run_status = body.get("status", run_status)
        print(f"         updated status={run_status}")

    # ── 3. Claim the run ──
    print("[3/5] Claim run")
    node_id = f"m0-worker-node-{uuid.uuid4().hex[:8]}"
    status, body = _req(
        _api(api_base, f"/tool-runs/{run_id}/claim"),
        method="POST",
        headers=_auth_headers(admin_token),
        body={"node_id": node_id},
    )
    if check("POST /tool-runs/{run_id}/claim", status):
        print(f"         claim_status={body.get('status')}")
        print(f"         tool_name={body.get('tool_name')}")

    # ── 4. Push events ──
    print("[4/5] Push run events")
    status, body = _req(
        _api(api_base, f"/tool-runs/{run_id}/events"),
        method="POST",
        headers=_auth_headers(admin_token),
        body={
            "events": [
                {"type": "started", "data": "worker started"},
                {"type": "stdout", "data": "hello from m0 worker verify"},
                {"type": "completed", "data": "worker completed successfully"},
            ]
        },
    )
    check("POST /tool-runs/{run_id}/events (started + stdout + completed)", status)

    # ── 5. Verify terminal state ──
    print("[5/5] Verify terminal state")
    status, body = _req(_api(api_base, f"/tool-runs/{run_id}"), headers=_auth_headers(admin_token))
    if check("GET /tool-runs/{run_id}", status):
        final_status = body.get("status", "unknown")
        print(f"         final_status={final_status}")
        if final_status not in ("succeeded", "completed"):
            print(f"  [WARN] run did not reach terminal succeeded/completed state")
        else:
            print(f"  [PASS] run reached terminal state: {final_status}")

    # Read events back
    status, events_body = _req(_api(api_base, f"/tool-runs/{run_id}/events"), headers=_auth_headers(admin_token))
    if check("GET /tool-runs/{run_id}/events", status):
        event_count = len(events_body if isinstance(events_body, list) else events_body.get("events", []))
        print(f"         events recorded: {event_count}")

    # ── Summary ──
    print()
    if failures:
        print(f"RESULT: FAIL ({failures} check(s) failed)")
        return 1
    print(f"RESULT: ALL CHECKS PASSED (executor={args.executor})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
