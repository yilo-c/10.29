from __future__ import annotations

from automation_hub.api.approvals.service import reconcile_pending_approvals
from automation_hub.api.config import settings
from automation_hub.api.db import get_db_connection, get_tool_run_by_id
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client, register_tool


def test_tools_and_tool_runs_with_approvals(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch, with_queue=True)

    client = make_client()
    headers = bootstrap_headers(client)
    me_response = client.get("/auth/me", headers=headers)
    assert me_response.status_code == 200, me_response.text
    user_id = me_response.json()["user_id"]

    register_tool(client, headers, "read_echo", "read")
    register_tool(client, headers, "danger_echo", "exec_high")

    list_response = client.get("/tools", headers=headers)
    assert list_response.status_code == 200, list_response.text
    assert list_response.json()["count"] >= 2

    disable_response = client.post("/tools/read_echo/disable", headers=headers)
    assert disable_response.status_code == 200, disable_response.text
    enable_response = client.post("/tools/read_echo/enable", headers=headers)
    assert enable_response.status_code == 200, enable_response.text

    run_response = client.post(
        "/tool-runs",
        headers=headers,
        json={"tool_id": "read_echo", "args": {}},
    )
    assert run_response.status_code == 200, run_response.text
    queued_run = run_response.json()
    assert queued_run["status"] == "queued"
    assert len(env["dummy_queue"].calls) == 1
    queue_args, queue_kwargs = env["dummy_queue"].calls[0]
    assert queue_args[0] == "arq_run_tool_job"
    assert queue_args[1] == (queued_run["run_id"], "read_echo", {}, user_id)
    assert queue_kwargs["job_timeout"] == 40

    pending_response = client.post(
        "/tool-runs",
        headers=headers,
        json={"tool_id": "danger_echo", "args": {}, "reason": "need approval"},
    )
    assert pending_response.status_code == 200, pending_response.text
    pending_run = pending_response.json()
    assert pending_run["status"] == "pending_approval"
    assert pending_run["approval_id"] is not None

    approvals_response = client.get("/approvals?status=pending", headers=headers)
    assert approvals_response.status_code == 200, approvals_response.text
    approvals_payload = approvals_response.json()
    assert approvals_payload["count"] >= 1

    approval_id = pending_run["approval_id"]
    approve_response = client.post(f"/approvals/{approval_id}/approve?note=ok", headers=headers)
    assert approve_response.status_code == 200, approve_response.text
    assert len(env["dummy_queue"].calls) == 2
    approve_queue_args, approve_queue_kwargs = env["dummy_queue"].calls[1]
    assert approve_queue_args[0] == "arq_run_tool_job"
    assert approve_queue_args[1] == (pending_run["run_id"], "danger_echo", {}, user_id)
    assert approve_queue_kwargs["job_timeout"] == 180

    run_detail_response = client.get(f"/tool-runs/{pending_run['run_id']}", headers=headers)
    assert run_detail_response.status_code == 200, run_detail_response.text
    assert run_detail_response.json()["status"] == "queued"

    audit_response = client.get("/audit?event_type=approval.decided&limit=10", headers=headers)
    assert audit_response.status_code == 200, audit_response.text
    events = audit_response.json()["events"]
    assert any(event["resource_id"] == approval_id and event["action"] == "approve" for event in events)


def test_tool_run_rejects_invalid_args_schema(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch, with_queue=True)

    client = make_client()
    headers = bootstrap_headers(client)

    register_tool(
        client,
        headers,
        "schema_guard",
        "read",
        args_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    run_response = client.post(
        "/tool-runs",
        headers=headers,
        json={"tool_id": "schema_guard", "args": {}},
    )
    assert run_response.status_code == 400, run_response.text
    assert "args_schema" in run_response.text
    assert len(env["dummy_queue"].calls) == 0


def test_tool_run_rejects_paths_outside_allowed_paths(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch, with_queue=True)

    client = make_client()
    headers = bootstrap_headers(client)

    register_tool(
        client,
        headers,
        "path_guard",
        "read",
        allowed_paths=["safe"],
    )

    run_response = client.post(
        "/tool-runs",
        headers=headers,
        json={"tool_id": "path_guard", "args": {"path": "../outside.txt"}},
    )
    assert run_response.status_code == 403, run_response.text
    assert "allowed_paths" in run_response.text
    assert len(env["dummy_queue"].calls) == 0


def test_tool_run_rejects_disabled_tool(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch, with_queue=True)

    client = make_client()
    headers = bootstrap_headers(client)

    register_tool(client, headers, "disabled_guard", "read")

    disable_response = client.post("/tools/disabled_guard/disable", headers=headers)
    assert disable_response.status_code == 200, disable_response.text

    run_response = client.post(
        "/tool-runs",
        headers=headers,
        json={"tool_id": "disabled_guard", "args": {}},
    )
    assert run_response.status_code == 404, run_response.text
    assert "tool not found" in run_response.text
    assert len(env["dummy_queue"].calls) == 0


def test_tool_run_requires_tool_execute_scope(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch, with_queue=True)

    client = make_client()
    admin_headers = bootstrap_headers(client)
    register_tool(client, admin_headers, "scope_guard", "read")

    device_response = client.post(
        "/auth/devices",
        headers=admin_headers,
        json={"name": "readonly device", "platform": "windows"},
    )
    assert device_response.status_code == 200, device_response.text
    device_id = device_response.json()["id"]

    token_response = client.post(
        "/auth/tokens",
        headers=admin_headers,
        json={"device_id": device_id, "template": "readonly"},
    )
    assert token_response.status_code == 200, token_response.text
    readonly_headers = {"Authorization": f"Bearer {token_response.json()['token']}"}

    run_response = client.post(
        "/tool-runs",
        headers=readonly_headers,
        json={"tool_id": "scope_guard", "args": {}},
    )
    assert run_response.status_code == 403, run_response.text
    assert "ReadOnly tokens blocked from write endpoints" in run_response.text
    assert len(env["dummy_queue"].calls) == 0


def test_cancelled_approval_moves_run_to_denied(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch, with_queue=True)

    client = make_client()
    headers = bootstrap_headers(client)

    register_tool(client, headers, "cancel_guard", "exec_high")

    pending_response = client.post(
        "/tool-runs",
        headers=headers,
        json={"tool_id": "cancel_guard", "args": {}, "reason": "cancel me"},
    )
    assert pending_response.status_code == 200, pending_response.text
    pending_run = pending_response.json()

    cancel_response = client.post(
        f"/approvals/{pending_run['approval_id']}/cancel?note=user_abort",
        headers=headers,
    )
    assert cancel_response.status_code == 200, cancel_response.text
    assert cancel_response.json()["cancelled"] is True

    approval_response = client.get(f"/approvals/{pending_run['approval_id']}", headers=headers)
    assert approval_response.status_code == 200, approval_response.text
    assert approval_response.json()["status"] == "cancelled"

    run_row = get_tool_run_by_id(pending_run["run_id"])
    assert run_row is not None
    assert run_row["status"] == "denied"
    assert "approval cancelled" in (run_row["error_msg"] or "")

    audit_response = client.get("/audit?event_type=approval.decided&limit=20", headers=headers)
    assert audit_response.status_code == 200, audit_response.text
    assert any(
        event["resource_id"] == pending_run["approval_id"] and event["action"] == "cancel"
        for event in audit_response.json()["events"]
    )


def test_expired_approval_marks_run_denied(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch, with_queue=True)
    monkeypatch.setattr(settings, "APPROVAL_TTL_SECONDS", 1)

    client = make_client()
    headers = bootstrap_headers(client)

    register_tool(client, headers, "expire_guard", "exec_high")

    pending_response = client.post(
        "/tool-runs",
        headers=headers,
        json={"tool_id": "expire_guard", "args": {}, "reason": "expire me"},
    )
    assert pending_response.status_code == 200, pending_response.text
    pending_run = pending_response.json()

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE approval_requests SET created_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", pending_run["approval_id"]),
        )
        conn.commit()

    expired = reconcile_pending_approvals()
    assert any(item["id"] == pending_run["approval_id"] for item in expired)

    approval_response = client.get(f"/approvals/{pending_run['approval_id']}", headers=headers)
    assert approval_response.status_code == 200, approval_response.text
    assert approval_response.json()["status"] == "expired"

    run_row = get_tool_run_by_id(pending_run["run_id"])
    assert run_row is not None
    assert run_row["status"] == "denied"
    assert "approval expired" in (run_row["error_msg"] or "")

    audit_response = client.get("/audit?event_type=approval.expired&limit=20", headers=headers)
    assert audit_response.status_code == 200, audit_response.text
    assert any(event["resource_id"] == pending_run["approval_id"] for event in audit_response.json()["events"])


def test_retry_cancelled_high_risk_run_creates_new_pending_approval(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch, with_queue=True)

    client = make_client()
    headers = bootstrap_headers(client)

    register_tool(client, headers, "retry_guard", "exec_high")

    original_response = client.post(
        "/tool-runs",
        headers=headers,
        json={"tool_id": "retry_guard", "args": {"path": "safe/file.txt"}, "reason": "first request"},
    )
    assert original_response.status_code == 200, original_response.text
    original_run = original_response.json()
    assert original_run["status"] == "pending_approval"

    cancel_response = client.post(
        f"/approvals/{original_run['approval_id']}/cancel?note=window_closed",
        headers=headers,
    )
    assert cancel_response.status_code == 200, cancel_response.text

    retry_response = client.post(
        f"/tool-runs/{original_run['run_id']}/retry",
        headers=headers,
        json={"reason": "retry after cancel"},
    )
    assert retry_response.status_code == 200, retry_response.text
    retried_run = retry_response.json()
    assert retried_run["run_id"] != original_run["run_id"]
    assert retried_run["status"] == "pending_approval"
    assert retried_run["approval_id"]
    assert retried_run["approval_id"] != original_run["approval_id"]

    new_run_row = get_tool_run_by_id(retried_run["run_id"])
    assert new_run_row is not None
    assert new_run_row["tool_id"] == "retry_guard"
    assert '"path": "safe/file.txt"' in (new_run_row["args_json"] or "")

    audit_response = client.get("/audit?event_type=run.retried&limit=20", headers=headers)
    assert audit_response.status_code == 200, audit_response.text
    assert any(
        event["resource_id"] == retried_run["run_id"] and original_run["run_id"] in event["meta_json"]
        for event in audit_response.json()["events"]
    )


def test_retry_rejects_non_terminal_run(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch, with_queue=True)

    client = make_client()
    headers = bootstrap_headers(client)

    register_tool(client, headers, "retry_pending_guard", "exec_high")

    original_response = client.post(
        "/tool-runs",
        headers=headers,
        json={"tool_id": "retry_pending_guard", "args": {}, "reason": "still pending"},
    )
    assert original_response.status_code == 200, original_response.text
    original_run = original_response.json()

    retry_response = client.post(
        f"/tool-runs/{original_run['run_id']}/retry",
        headers=headers,
        json={"reason": "too early"},
    )
    assert retry_response.status_code == 409, retry_response.text
    assert "retryable terminal state" in retry_response.text


def test_denied_approval_moves_run_to_denied_and_notifies(tmp_path, monkeypatch):
    """P0-5：审批拒绝 → tool_runs.status='denied' + 通知被调用。

    覆盖与 simple_executor / mobile_ws 行为一致性：
      - decide_approval 已置 tool_runs.status='denied'（通过 _advance_linked_resource_to_terminal）
      - _notify_approval_decision 被调用（通过 monkeypatch 注入计数）
    """
    env = configure_test_env(tmp_path, monkeypatch, with_queue=True)

    # P0-5：拦截通知函数，验证调用次数与 payload
    # 注意：由于 automation_hub/api/main.py 使用相对导入 ``from .approvals.router``，
    # sys.modules 中同时存在 ``api.approvals.router`` 与 ``automation_hub.api.approvals.router``
    # 两个独立实例。FastAPI 路由实际挂在 ``api.approvals.router`` 上，
    # 必须把两个实例的 notify_user_sync 都替换掉。
    import sys
    notify_calls: list[dict] = []
    def _fake_notify_user_sync(user_id, message):
        notify_calls.append({"user_id": user_id, "message": message})
        return 0
    for _mod_key in list(sys.modules):
        if _mod_key.endswith(("approvals.router",)) and hasattr(sys.modules[_mod_key], "notify_user_sync"):
            monkeypatch.setattr(sys.modules[_mod_key], "notify_user_sync", _fake_notify_user_sync)

    client = make_client()
    headers = bootstrap_headers(client)
    me_response = client.get("/auth/me", headers=headers)
    assert me_response.status_code == 200, me_response.text
    user_id = me_response.json()["user_id"]

    register_tool(client, headers, "deny_guard", "exec_high")

    pending_response = client.post(
        "/tool-runs",
        headers=headers,
        json={"tool_id": "deny_guard", "args": {}, "reason": "deny me"},
    )
    assert pending_response.status_code == 200, pending_response.text
    pending_run = pending_response.json()
    assert pending_run["status"] == "pending_approval"

    # 拒绝审批
    deny_response = client.post(
        f"/approvals/{pending_run['approval_id']}/deny?note=risk_too_high",
        headers=headers,
    )
    assert deny_response.status_code == 200, deny_response.text
    assert deny_response.json()["denied"] is True

    # 拒绝路径不应触发入队（与 simple_executor 行为一致）
    assert len(env["dummy_queue"].calls) == 0

    # 验证 tool_runs.status='denied' + error_msg 含拒绝原因
    run_row = get_tool_run_by_id(pending_run["run_id"])
    assert run_row is not None
    assert run_row["status"] == "denied"
    assert "approval denied" in (run_row["error_msg"] or "")
    assert run_row["finished_at"] is not None

    # 验证审批请求状态
    approval_response = client.get(f"/approvals/{pending_run['approval_id']}", headers=headers)
    assert approval_response.status_code == 200, approval_response.text
    assert approval_response.json()["status"] == "denied"

    # P0-5：通知被触发一次，目标是发起人，decision=denied
    assert len(notify_calls) == 1, f"expected 1 notify call, got {len(notify_calls)}"
    assert notify_calls[0]["user_id"] == user_id
    msg = notify_calls[0]["message"]
    assert msg["type"] == "approval_decided"
    assert msg["decision"] == "denied"
    assert msg["resource_id"] == pending_run["run_id"]
    assert msg["note"] == "risk_too_high"

    # 审计日志记录拒绝
    audit_response = client.get("/audit?event_type=approval.decided&limit=20", headers=headers)
    assert audit_response.status_code == 200, audit_response.text
    assert any(
        event["resource_id"] == pending_run["approval_id"] and event["action"] == "deny"
        for event in audit_response.json()["events"]
    )


def test_approve_triggers_notify_and_resume(tmp_path, monkeypatch):
    """P0-5：审批通过 → run 续跑（入队） + 通知被调用。"""
    env = configure_test_env(tmp_path, monkeypatch, with_queue=True)

    # 同上：需 patch 两个模块实例的 notify_user_sync
    import sys
    notify_calls: list[dict] = []
    def _fake_notify_user_sync(user_id, message):
        notify_calls.append({"user_id": user_id, "message": message})
        return 0
    for _mod_key in list(sys.modules):
        if _mod_key.endswith(("approvals.router",)) and hasattr(sys.modules[_mod_key], "notify_user_sync"):
            monkeypatch.setattr(sys.modules[_mod_key], "notify_user_sync", _fake_notify_user_sync)

    client = make_client()
    headers = bootstrap_headers(client)
    me_response = client.get("/auth/me", headers=headers)
    assert me_response.status_code == 200, me_response.text
    user_id = me_response.json()["user_id"]

    register_tool(client, headers, "approve_notify_guard", "exec_high")

    pending_response = client.post(
        "/tool-runs",
        headers=headers,
        json={"tool_id": "approve_notify_guard", "args": {}, "reason": "approve me"},
    )
    assert pending_response.status_code == 200, pending_response.text
    pending_run = pending_response.json()
    assert pending_run["status"] == "pending_approval"

    # 通过审批
    approve_response = client.post(
        f"/approvals/{pending_run['approval_id']}/approve?note=looks_safe",
        headers=headers,
    )
    assert approve_response.status_code == 200, approve_response.text

    # 续跑入队（与 simple_executor 行为对齐）
    assert len(env["dummy_queue"].calls) == 1
    queue_args, queue_kwargs = env["dummy_queue"].calls[0]
    assert queue_args[0] == "arq_run_tool_job"
    assert queue_args[1][0] == pending_run["run_id"]

    # run 状态变为 queued
    run_detail = client.get(f"/tool-runs/{pending_run['run_id']}", headers=headers)
    assert run_detail.status_code == 200, run_detail.text
    assert run_detail.json()["status"] == "queued"

    # 通知被触发
    assert len(notify_calls) == 1
    msg = notify_calls[0]["message"]
    assert msg["type"] == "approval_decided"
    assert msg["decision"] == "approved"
    assert msg["run_resumed"] is True
    assert notify_calls[0]["user_id"] == user_id