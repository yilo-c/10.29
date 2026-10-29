from __future__ import annotations

import json
import subprocess
import sys

from automation_hub.api.approvals.service import reconcile_pending_approvals
from automation_hub.api.config import settings
from automation_hub.api.db import get_db_connection
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


def test_proposals_repo_index_and_legacy_runs(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch, with_queue=True)
    repo_root = env["workspace_dir"] / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    tracked_file = repo_root / "notes.txt"
    tracked_file.write_text("proposal regression search needle\n", encoding="utf-8")

    client = make_client()
    headers = bootstrap_headers(client)

    register_tool(client, headers, "verify_echo", "read")
    register_tool(client, headers, "legacy_echo", "read")

    proposal_response = client.post(
        "/proposals",
        headers=headers,
        json={
            "title": "Patch notes",
            "summary": "exercise proposal api",
            "plan_md": "1. run verify tool",
            "verify_tools": [{"tool_id": "verify_echo", "args": {}}],
            "risk_level": "write",
        },
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal = proposal_response.json()
    assert proposal["status"] == "draft"

    proposals_response = client.get("/proposals", headers=headers)
    assert proposals_response.status_code == 200, proposals_response.text
    assert any(item["id"] == proposal["id"] for item in proposals_response.json())

    submit_response = client.post(f"/proposals/{proposal['id']}/submit", headers=headers)
    assert submit_response.status_code == 200, submit_response.text
    submit_payload = submit_response.json()
    assert submit_payload["submitted"] is True
    assert submit_payload["approval_id"]

    repo_response = client.post(
        "/repos",
        headers=headers,
        json={"name": "tmp-repo", "root_path": str(repo_root)},
    )
    assert repo_response.status_code == 200, repo_response.text
    repo = repo_response.json()

    repos_response = client.get("/repos", headers=headers)
    assert repos_response.status_code == 200, repos_response.text
    assert any(item["id"] == repo["id"] for item in repos_response.json())

    index_response = client.post(f"/repos/{repo['id']}/index", headers=headers)
    assert index_response.status_code == 200, index_response.text
    assert index_response.json()["files"] >= 1

    files_response = client.get(f"/repos/{repo['id']}/files", headers=headers)
    assert files_response.status_code == 200, files_response.text
    files_payload = files_response.json()
    assert files_payload["total"] >= 1
    assert any(item["path"] == "notes.txt" for item in files_payload["files"])

    search_response = client.get(f"/repos/{repo['id']}/search?q=needle", headers=headers)
    assert search_response.status_code == 200, search_response.text
    search_payload = search_response.json()
    assert search_payload["engine"] in {"rg", "python"}
    assert search_payload["total"] >= 1
    assert any("notes.txt" in item for item in search_payload["matches"])
    assert any(item["path"] == "notes.txt" and item["line"] >= 1 for item in search_payload["items"])

    legacy_run_response = client.post(
        "/runs/",
        headers=headers,
        json={"script_name": "legacy_echo", "parameters": {}},
    )
    assert legacy_run_response.status_code == 200, legacy_run_response.text
    legacy_run = legacy_run_response.json()
    assert legacy_run["status"] == "queued"
    assert len(env["dummy_queue"].calls) == 1

    legacy_list_response = client.get("/runs/", headers=headers)
    assert legacy_list_response.status_code == 200, legacy_list_response.text
    assert any(item["run_id"] == legacy_run["run_id"] for item in legacy_list_response.json())


def test_proposal_apply_marks_applied_and_persists_meta(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch, with_queue=True)
    repo_root = env["workspace_dir"]
    repo_root.mkdir(parents=True, exist_ok=True)
    tracked_file = repo_root / "notes.txt"
    tracked_file.write_text("base line\n", encoding="utf-8")
    _init_git_repo(repo_root)
    patch_text = _build_patch(repo_root, "notes.txt", "base line\nnew line\n")

    client = make_client()
    headers = bootstrap_headers(client)

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
            "title": "Apply patch",
            "summary": "apply should succeed",
            "plan_md": "1. apply diff\n2. verify",
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
    assert apply_response.json()["applied"] is True

    assert tracked_file.read_text(encoding="utf-8") == "base line\nnew line\n"

    proposal_detail = client.get(f"/proposals/{proposal_id}", headers=headers)
    assert proposal_detail.status_code == 200, proposal_detail.text
    assert proposal_detail.json()["status"] == "applied"

    proposal_dir = env["data_dir"] / "proposals" / proposal_id
    assert (proposal_dir / "patch.applied.diff").exists()
    assert (proposal_dir / "apply_meta.json").exists()

    audit_response = client.get("/audit?event_type=proposal.applied&limit=10", headers=headers)
    assert audit_response.status_code == 200, audit_response.text
    assert any(event["resource_id"] == proposal_id for event in audit_response.json()["events"])


def test_proposal_apply_verify_failure_rolls_back(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch, with_queue=True)
    repo_root = env["workspace_dir"]
    repo_root.mkdir(parents=True, exist_ok=True)
    tracked_file = repo_root / "notes.txt"
    tracked_file.write_text("base line\n", encoding="utf-8")
    _init_git_repo(repo_root)
    patch_text = _build_patch(repo_root, "notes.txt", "base line\nshould rollback\n")

    client = make_client()
    headers = bootstrap_headers(client)

    register_tool(
        client,
        headers,
        "verify_fail",
        "read",
        command=[sys.executable, "-c", "import sys; sys.exit(1)"],
        cwd=str(repo_root),
    )

    proposal_response = client.post(
        "/proposals",
        headers=headers,
        json={
            "title": "Rollback patch",
            "summary": "verify should fail",
            "plan_md": "1. apply diff\n2. fail verify",
            "patch_diff": patch_text,
            "verify_tools": [{"tool_id": "verify_fail", "args": {}}],
            "risk_level": "write",
        },
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal_id = proposal_response.json()["id"]

    submit_response = client.post(f"/proposals/{proposal_id}/submit", headers=headers)
    assert submit_response.status_code == 200, submit_response.text
    approval_id = submit_response.json()["approval_id"]

    approve_response = client.post(f"/approvals/{approval_id}/approve?note=try", headers=headers)
    assert approve_response.status_code == 200, approve_response.text

    apply_response = client.post(f"/proposals/{proposal_id}/apply", headers=headers)
    assert apply_response.status_code == 400, apply_response.text
    assert "verify failed" in apply_response.text

    assert tracked_file.read_text(encoding="utf-8") == "base line\n"

    proposal_detail = client.get(f"/proposals/{proposal_id}", headers=headers)
    assert proposal_detail.status_code == 200, proposal_detail.text
    assert proposal_detail.json()["status"] == "rejected"

    audit_response = client.get("/audit?event_type=proposal.rolled_back&limit=10", headers=headers)
    assert audit_response.status_code == 200, audit_response.text
    assert any(event["resource_id"] == proposal_id for event in audit_response.json()["events"])


def test_proposal_apply_requires_approved_submission(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    repo_root = env["workspace_dir"]
    repo_root.mkdir(parents=True, exist_ok=True)
    tracked_file = repo_root / "notes.txt"
    tracked_file.write_text("base line\n", encoding="utf-8")
    _init_git_repo(repo_root)
    patch_text = _build_patch(repo_root, "notes.txt", "base line\nblocked apply\n")

    client = make_client()
    headers = bootstrap_headers(client)

    proposal_response = client.post(
        "/proposals",
        headers=headers,
        json={
            "title": "Blocked apply",
            "summary": "apply should require approval",
            "plan_md": "1. submit\n2. try apply before approval",
            "patch_diff": patch_text,
            "verify_tools": [],
            "risk_level": "write",
        },
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal_id = proposal_response.json()["id"]

    submit_response = client.post(f"/proposals/{proposal_id}/submit", headers=headers)
    assert submit_response.status_code == 200, submit_response.text

    apply_response = client.post(f"/proposals/{proposal_id}/apply", headers=headers)
    assert apply_response.status_code == 403, apply_response.text
    assert "proposal not approved" in apply_response.text

    assert tracked_file.read_text(encoding="utf-8") == "base line\n"

    proposal_detail = client.get(f"/proposals/{proposal_id}", headers=headers)
    assert proposal_detail.status_code == 200, proposal_detail.text
    assert proposal_detail.json()["status"] == "pending_approval"


def test_proposal_apply_rejects_invalid_patch_before_apply(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    repo_root = env["workspace_dir"]
    repo_root.mkdir(parents=True, exist_ok=True)
    tracked_file = repo_root / "notes.txt"
    tracked_file.write_text("base line\n", encoding="utf-8")
    _init_git_repo(repo_root)

    invalid_patch = """diff --git a/notes.txt b/notes.txt
index 0000000..1111111 100644
--- a/notes.txt
+++ b/notes.txt
@@ -1 +1 @@
-missing base line
+new line
"""

    client = make_client()
    headers = bootstrap_headers(client)

    proposal_response = client.post(
        "/proposals",
        headers=headers,
        json={
            "title": "Invalid patch",
            "summary": "patch check should fail",
            "plan_md": "1. submit\n2. approve\n3. fail patch check",
            "patch_diff": invalid_patch,
            "verify_tools": [],
            "risk_level": "write",
        },
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal_id = proposal_response.json()["id"]

    submit_response = client.post(f"/proposals/{proposal_id}/submit", headers=headers)
    assert submit_response.status_code == 200, submit_response.text
    approval_id = submit_response.json()["approval_id"]

    approve_response = client.post(f"/approvals/{approval_id}/approve?note=attempt", headers=headers)
    assert approve_response.status_code == 200, approve_response.text

    apply_response = client.post(f"/proposals/{proposal_id}/apply", headers=headers)
    assert apply_response.status_code == 400, apply_response.text
    assert "patch check failed" in apply_response.text

    assert tracked_file.read_text(encoding="utf-8") == "base line\n"

    proposal_detail = client.get(f"/proposals/{proposal_id}", headers=headers)
    assert proposal_detail.status_code == 200, proposal_detail.text
    assert proposal_detail.json()["status"] == "rejected"

    proposal_dir = env["data_dir"] / "proposals" / proposal_id
    assert not (proposal_dir / "patch.applied.diff").exists()

    audit_response = client.get("/audit?event_type=proposal.rolled_back&limit=10", headers=headers)
    assert audit_response.status_code == 200, audit_response.text
    assert any(event["resource_id"] == proposal_id for event in audit_response.json()["events"])


def test_proposal_create_rejects_legacy_verify_commands_strings(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    proposal_response = client.post(
        "/proposals",
        headers=headers,
        json={
            "title": "Legacy verify commands",
            "summary": "string verify commands should be rejected",
            "plan_md": "1. reject legacy field",
            "patch_diff": None,
            "verify_commands": ["echo unsafe"],
            "verify_tools": [],
            "risk_level": "write",
        },
    )
    assert proposal_response.status_code == 400, proposal_response.text
    assert "verify_commands" in proposal_response.text


def test_proposal_create_persists_repo_context_snapshot(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    repo_root = env["workspace_dir"] / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "notes.txt").write_text("proposal needle one\nproposal needle two\n", encoding="utf-8")

    client = make_client()
    headers = bootstrap_headers(client)

    repo_response = client.post(
        "/repos",
        headers=headers,
        json={"name": "proposal-repo", "root_path": str(repo_root)},
    )
    assert repo_response.status_code == 200, repo_response.text
    repo_id = repo_response.json()["id"]

    proposal_response = client.post(
        "/proposals",
        headers=headers,
        json={
            "title": "Proposal with repo context",
            "summary": "capture repo evidence",
            "plan_md": "1. inspect repo\n2. draft proposal",
            "patch_diff": None,
            "verify_tools": [],
            "risk_level": "write",
            "repo_context": {"repo_id": repo_id, "query": "needle", "limit": 5},
        },
    )
    assert proposal_response.status_code == 200, proposal_response.text
    payload = proposal_response.json()
    proposal_id = payload["id"]
    assert payload["repo_context"]["repo_id"] == repo_id
    assert payload["repo_context"]["repo_name"] == "proposal-repo"
    assert payload["repo_context"]["query"] == "needle"
    assert payload["repo_context"]["total"] == 2
    assert payload["repo_context"]["injected_files"] == ["notes.txt", "notes.txt"]
    assert payload["repo_context"]["items"] == [
        {"path": "notes.txt", "line": 1, "text": "proposal needle one"},
        {"path": "notes.txt", "line": 2, "text": "proposal needle two"},
    ]

    proposal_dir = env["data_dir"] / "proposals" / proposal_id
    snapshot_path = proposal_dir / "repo_context.json"
    assert snapshot_path.exists()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["repo_id"] == repo_id
    assert snapshot["query"] == "needle"
    assert snapshot["items"][0]["path"] == "notes.txt"

    detail_response = client.get(f"/proposals/{proposal_id}", headers=headers)
    assert detail_response.status_code == 200, detail_response.text
    assert detail_response.json()["repo_context"]["repo_id"] == repo_id

    audit_response = client.get("/audit?event_type=proposal.created&limit=20", headers=headers)
    assert audit_response.status_code == 200, audit_response.text
    created_event = next(event for event in audit_response.json()["events"] if event["resource_id"] == proposal_id)
    meta = json.loads(created_event["meta_json"])
    assert meta["target_repo_id"] == repo_id
    assert meta["repo_context"]["query"] == "needle"


def test_proposal_create_repo_context_requires_repo_read_scope(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    admin_headers = bootstrap_headers(client)

    repo_root = env["workspace_dir"] / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "notes.txt").write_text("needle\n", encoding="utf-8")

    repo_response = client.post(
        "/repos",
        headers=admin_headers,
        json={"name": "proposal-repo", "root_path": str(repo_root)},
    )
    assert repo_response.status_code == 200, repo_response.text
    repo_id = repo_response.json()["id"]

    device_response = client.post(
        "/auth/devices",
        headers=admin_headers,
        json={"name": "proposal-only device", "platform": "windows"},
    )
    assert device_response.status_code == 200, device_response.text

    token_response = client.post(
        "/auth/tokens",
        headers=admin_headers,
        json={
            "device_id": device_response.json()["id"],
            "scopes": ["proposal:write"],
        },
    )
    assert token_response.status_code == 200, token_response.text
    proposal_headers = {"Authorization": f"Bearer {token_response.json()['token']}"}

    proposal_response = client.post(
        "/proposals",
        headers=proposal_headers,
        json={
            "title": "Proposal without repo read",
            "summary": "should fail",
            "plan_md": "1. reject missing repo scope",
            "patch_diff": None,
            "verify_tools": [],
            "risk_level": "write",
            "repo_context": {"repo_id": repo_id, "query": "needle", "limit": 5},
        },
    )
    assert proposal_response.status_code == 403, proposal_response.text
    assert "missing scope: repo:read" in proposal_response.text


def test_expired_proposal_approval_marks_proposal_rejected(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    repo_root = env["workspace_dir"]
    repo_root.mkdir(parents=True, exist_ok=True)
    tracked_file = repo_root / "notes.txt"
    tracked_file.write_text("base line\n", encoding="utf-8")
    _init_git_repo(repo_root)
    patch_text = _build_patch(repo_root, "notes.txt", "base line\nexpired approval\n")

    monkeypatch.setattr(settings, "APPROVAL_TTL_SECONDS", 1)

    client = make_client()
    headers = bootstrap_headers(client)

    proposal_response = client.post(
        "/proposals",
        headers=headers,
        json={
            "title": "Expired proposal",
            "summary": "pending approval should expire",
            "plan_md": "1. submit\n2. expire approval",
            "patch_diff": patch_text,
            "verify_tools": [],
            "risk_level": "write",
        },
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal_id = proposal_response.json()["id"]

    submit_response = client.post(f"/proposals/{proposal_id}/submit", headers=headers)
    assert submit_response.status_code == 200, submit_response.text
    approval_id = submit_response.json()["approval_id"]

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE approval_requests SET created_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", approval_id),
        )
        conn.commit()

    expired = reconcile_pending_approvals()
    assert any(item["id"] == approval_id for item in expired)

    approval_response = client.get(f"/approvals/{approval_id}", headers=headers)
    assert approval_response.status_code == 200, approval_response.text
    assert approval_response.json()["status"] == "expired"

    proposal_detail = client.get(f"/proposals/{proposal_id}", headers=headers)
    assert proposal_detail.status_code == 200, proposal_detail.text
    assert proposal_detail.json()["status"] == "rejected"

    apply_response = client.post(f"/proposals/{proposal_id}/apply", headers=headers)
    assert apply_response.status_code == 403, apply_response.text
    assert "proposal not approved" in apply_response.text


def test_retry_rejected_proposal_creates_new_pending_approval(tmp_path, monkeypatch):
    env = configure_test_env(tmp_path, monkeypatch)
    repo_root = env["workspace_dir"]
    repo_root.mkdir(parents=True, exist_ok=True)
    tracked_file = repo_root / "notes.txt"
    tracked_file.write_text("base line\n", encoding="utf-8")
    _init_git_repo(repo_root)
    patch_text = _build_patch(repo_root, "notes.txt", "base line\nretry apply\n")

    monkeypatch.setattr(settings, "APPROVAL_TTL_SECONDS", 1)

    client = make_client()
    headers = bootstrap_headers(client)

    proposal_response = client.post(
        "/proposals",
        headers=headers,
        json={
            "title": "Retry proposal",
            "summary": "proposal should support explicit retry",
            "plan_md": "1. submit\n2. expire\n3. retry",
            "patch_diff": patch_text,
            "verify_tools": [],
            "risk_level": "write",
        },
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal_id = proposal_response.json()["id"]

    submit_response = client.post(f"/proposals/{proposal_id}/submit", headers=headers)
    assert submit_response.status_code == 200, submit_response.text
    first_approval_id = submit_response.json()["approval_id"]

    with get_db_connection() as conn:
        conn.execute(
            "UPDATE approval_requests SET created_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", first_approval_id),
        )
        conn.commit()

    expired = reconcile_pending_approvals()
    assert any(item["id"] == first_approval_id for item in expired)

    proposal_detail = client.get(f"/proposals/{proposal_id}", headers=headers)
    assert proposal_detail.status_code == 200, proposal_detail.text
    assert proposal_detail.json()["status"] == "rejected"

    retry_response = client.post(
        f"/proposals/{proposal_id}/retry",
        headers=headers,
        json={"reason": "retry after expiration"},
    )
    assert retry_response.status_code == 200, retry_response.text
    retry_payload = retry_response.json()
    assert retry_payload["action"] == "retry"
    assert retry_payload["approval_id"] != first_approval_id

    proposal_detail = client.get(f"/proposals/{proposal_id}", headers=headers)
    assert proposal_detail.status_code == 200, proposal_detail.text
    assert proposal_detail.json()["status"] == "pending_approval"

    approvals_response = client.get("/approvals?status=pending", headers=headers)
    assert approvals_response.status_code == 200, approvals_response.text
    assert any(item["id"] == retry_payload["approval_id"] for item in approvals_response.json()["approvals"])

    audit_response = client.get("/audit?event_type=proposal.retried&limit=20", headers=headers)
    assert audit_response.status_code == 200, audit_response.text
    assert any(event["resource_id"] == proposal_id for event in audit_response.json()["events"])


def test_retry_rejects_non_rejected_proposal(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    proposal_response = client.post(
        "/proposals",
        headers=headers,
        json={
            "title": "Draft proposal",
            "summary": "retry should reject draft",
            "plan_md": "1. stay draft",
            "patch_diff": None,
            "verify_tools": [],
            "risk_level": "write",
        },
    )
    assert proposal_response.status_code == 200, proposal_response.text
    proposal_id = proposal_response.json()["id"]

    retry_response = client.post(
        f"/proposals/{proposal_id}/retry",
        headers=headers,
        json={"reason": "too early"},
    )
    assert retry_response.status_code == 409, retry_response.text
    assert "proposal not in rejected state" in retry_response.text