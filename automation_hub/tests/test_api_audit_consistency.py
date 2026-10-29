from __future__ import annotations

import itertools
import json
import subprocess
import sys
from datetime import datetime

from automation_hub.api.audit import service as audit_service
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client, register_tool


def _init_git_repo(repo_root):
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=repo_root, check=True)


def _build_patch(repo_root, file_name: str, updated_text: str) -> str:
    file_path = repo_root / file_name
    original_text = file_path.read_text(encoding="utf-8")
    subprocess.run(["git", "add", file_name], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo_root, check=True, capture_output=True, text=True)
    file_path.write_text(updated_text, encoding="utf-8")
    diff = subprocess.run(["git", "diff", "--", file_name], cwd=repo_root, check=True, capture_output=True, text=True).stdout
    file_path.write_text(original_text, encoding="utf-8")
    return diff


def test_audit_events_are_consistent_across_proposal_apply_flow(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    repo_root = env["workspace_dir"]
    repo_root.mkdir(parents=True, exist_ok=True)
    tracked_file = repo_root / "notes.txt"
    tracked_file.write_text("base line\n", encoding="utf-8")
    _init_git_repo(repo_root)
    patch_text = _build_patch(repo_root, "notes.txt", "base line\nnew line\n")

    client = make_client()
    headers = bootstrap_headers(client)

    me_response = client.get("/auth/me", headers=headers)
    assert me_response.status_code == 200, me_response.text
    user_id = me_response.json()["user_id"]

    register_tool(
        client,
        headers,
        "verify_ok",
        "read",
        command=[sys.executable, "-c", "print('verify ok')"],
        cwd=str(repo_root),
    )

    proposal_response = client.post(
        "/proposals",
        headers=headers,
        json={
            "title": "Audit chain",
            "summary": "ensure audit consistency",
            "plan_md": "1. create\n2. submit\n3. approve\n4. apply",
            "patch_diff": patch_text,
            "verify_tools": [{"tool_id": "verify_ok", "args": {}}],
            "risk_level": "write",
        },
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal_id = proposal_response.json()["id"]

    submit_response = client.post(f"/proposals/{proposal_id}/submit", headers=headers)
    assert submit_response.status_code == 200, submit_response.text
    approval_id = submit_response.json()["approval_id"]

    approve_response = client.post(f"/approvals/{approval_id}/approve?note=ship", headers=headers)
    assert approve_response.status_code == 200, approve_response.text

    apply_response = client.post(f"/proposals/{proposal_id}/apply", headers=headers)
    assert apply_response.status_code == 200, apply_response.text

    audit_response = client.get(f"/audit?actor_user_id={user_id}&limit=50", headers=headers)
    assert audit_response.status_code == 200, audit_response.text
    events = audit_response.json()["events"]

    proposal_events = [
        event for event in events
        if event["resource_type"] == "proposal" and event["resource_id"] == proposal_id
    ]
    assert {event["event_type"] for event in proposal_events} >= {
        "proposal.created",
        "proposal.submitted",
        "proposal.applied",
    }
    assert all(event["actor_user_id"] == user_id for event in proposal_events)

    approval_events = [
        event for event in events
        if event["resource_type"] == "approval" and event["resource_id"] == approval_id
    ]
    assert any(event["event_type"] == "approval.decided" and event["action"] == "approve" for event in approval_events)

    run_events = [
        event for event in events
        if event["resource_type"] == "run" and event["event_type"] == "run.executed"
    ]
    assert any("verify ok" in (event.get("message") or "") or event["resource_id"] for event in run_events)

    timeline = []
    for event in events:
        if event["resource_type"] == "proposal" and event["resource_id"] == proposal_id:
            timeline.append((event["event_type"], datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))))
        elif event["resource_type"] == "approval" and event["resource_id"] == approval_id and event["event_type"] == "approval.decided":
            timeline.append((event["event_type"], datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))))

    timeline.sort(key=lambda item: item[1])
    assert [name for name, _ in timeline] == [
        "proposal.created",
        "proposal.submitted",
        "approval.decided",
        "proposal.applied",
    ]

    submitted_event = next(event for event in proposal_events if event["event_type"] == "proposal.submitted")
    submitted_meta = json.loads(submitted_event["meta_json"])
    assert submitted_meta["approval_id"] == approval_id

    applied_event = next(event for event in proposal_events if event["event_type"] == "proposal.applied")
    applied_meta = json.loads(applied_event["meta_json"])
    assert applied_meta["before_head"]
    assert applied_meta["after_head"]
    assert isinstance(applied_meta["verify"], list)
    assert applied_meta["verify"][0]["tool_id"] == "verify_ok"

    approved_event = next(event for event in approval_events if event["event_type"] == "approval.decided")
    approved_meta = json.loads(approved_event["meta_json"])
    assert approved_meta["decision"] == "approved"
    assert approved_meta["note"] == "ship"

    executed_event = next(event for event in run_events if event["event_type"] == "run.executed")
    executed_meta = json.loads(executed_event["meta_json"])
    assert executed_meta["tool_id"] == "verify_ok"
    assert executed_meta["exit_code"] == 0

    assert tracked_file.read_text(encoding="utf-8") == "base line\nnew line\n"


def test_audit_query_combines_filters_and_offset_pagination(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    me_response = client.get("/auth/me", headers=headers)
    assert me_response.status_code == 200, me_response.text
    user_id = me_response.json()["user_id"]

    timeline = itertools.cycle([
        "2026-03-10T10:00:01+00:00",
        "2026-03-10T10:00:02+00:00",
        "2026-03-10T10:00:03+00:00",
        "2026-03-10T10:00:04+00:00",
    ])
    monkeypatch.setattr(audit_service, "now_iso", lambda: next(timeline))

    audit_service.log_event(
        event_type="audit.synthetic",
        action="approve",
        status="success",
        actor_user_id=user_id,
        resource_type="proposal",
        resource_id="proposal-1",
        message="first matching event",
        meta={"seq": 1},
    )
    audit_service.log_event(
        event_type="audit.synthetic",
        action="approve",
        status="success",
        actor_user_id=user_id,
        resource_type="proposal",
        resource_id="proposal-2",
        message="second matching event",
        meta={"seq": 2},
    )
    audit_service.log_event(
        event_type="audit.synthetic",
        action="deny",
        status="fail",
        actor_user_id=user_id,
        resource_type="proposal",
        resource_id="proposal-3",
        message="filtered out by action/status",
        meta={"seq": 3},
    )
    audit_service.log_event(
        event_type="audit.synthetic",
        action="approve",
        status="success",
        actor_user_id=user_id,
        resource_type="run",
        resource_id="run-1",
        message="filtered out by resource_type",
        meta={"seq": 4},
    )

    page_one = client.get(
        f"/audit?actor_user_id={user_id}&event_type=audit.synthetic&resource_type=proposal&action=approve&status=success&limit=1&offset=0",
        headers=headers,
    )
    assert page_one.status_code == 200, page_one.text
    page_one_payload = page_one.json()
    assert page_one_payload["count"] == 1
    assert page_one_payload["events"][0]["resource_id"] == "proposal-2"
    assert json.loads(page_one_payload["events"][0]["meta_json"])["seq"] == 2

    page_two = client.get(
        f"/audit?actor_user_id={user_id}&event_type=audit.synthetic&resource_type=proposal&action=approve&status=success&limit=1&offset=1",
        headers=headers,
    )
    assert page_two.status_code == 200, page_two.text
    page_two_payload = page_two.json()
    assert page_two_payload["count"] == 1
    assert page_two_payload["events"][0]["resource_id"] == "proposal-1"
    assert json.loads(page_two_payload["events"][0]["meta_json"])["seq"] == 1

    exact_match = client.get(
        f"/audit?actor_user_id={user_id}&event_type=audit.synthetic&resource_type=proposal&resource_id=proposal-1&action=approve&status=success&limit=10&offset=0",
        headers=headers,
    )
    assert exact_match.status_code == 200, exact_match.text
    exact_payload = exact_match.json()
    assert exact_payload["count"] == 1
    assert exact_payload["events"][0]["resource_id"] == "proposal-1"