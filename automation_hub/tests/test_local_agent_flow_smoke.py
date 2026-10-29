from __future__ import annotations

from fastapi.testclient import TestClient

from automation_hub.api.main import app
from automation_hub.scripts.local_agent_flow_smoke import main as run_local_agent_smoke, run_smoke_flow
from automation_hub.tests.support import configure_test_env


def test_local_agent_flow_smoke(tmp_path, monkeypatch, capsys):
    configure_test_env(tmp_path, monkeypatch, with_queue=False)

    run_local_agent_smoke()
    output = capsys.readouterr().out

    assert "SMOKE_OK" in output
    assert "node_id=" in output
    assert "run_id=" in output
    assert "events=" in output


def test_local_agent_flow_step_status_and_counts(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch, with_queue=False)

    result = run_smoke_flow()

    expected_steps = [
        "register",
        "heartbeat",
        "policy",
        "create_run",
        "claim",
        "push_events",
        "upload_artifact",
        "list_events",
        "list_artifacts",
    ]
    for step in expected_steps:
        assert result.step_status_codes.get(step) == 200

    assert result.node_id
    assert result.run_id
    assert result.artifact_id
    assert result.events_count == 2
    assert result.artifacts_count == 1


def test_artifact_metadata_and_download(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch, with_queue=False)
    result = run_smoke_flow()

    with TestClient(app) as client:
        # 创建只读 token 用于读取 run events/artifacts
        import json
        import uuid
        from datetime import datetime, timezone

        from api.auth.deps import hash_token
        from api.db import get_db_connection

        token = f"smoke-read-{uuid.uuid4()}"
        token_hash = hash_token(token)
        user_id = f"u-{uuid.uuid4()}"
        now = datetime.now(timezone.utc).isoformat()
        with get_db_connection() as conn:
            conn.execute("INSERT INTO users(id, name, created_at) VALUES(?,?,?)", (user_id, "read-user", now))
            conn.execute(
                """INSERT INTO api_tokens(id, user_id, token_hash, scopes, created_at)
                   VALUES(?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    user_id,
                    token_hash,
                    json.dumps(["tool:read"]),
                    now,
                ),
            )
            conn.commit()

        headers = {"Authorization": f"Bearer {token}"}

        meta_resp = client.get(f"/tool-runs/{result.run_id}/artifacts/{result.artifact_id}", headers=headers)
        assert meta_resp.status_code == 200, meta_resp.text
        meta = meta_resp.json()
        assert meta["id"] == result.artifact_id
        assert meta["filename"] == "smoke.txt"
        assert "data" not in meta

        dl_resp = client.get(
            f"/tool-runs/{result.run_id}/artifacts/{result.artifact_id}/download?disposition=attachment",
            headers=headers,
        )
        assert dl_resp.status_code == 200, dl_resp.text
        assert dl_resp.headers.get("content-type", "").startswith("text/plain")
        assert "attachment;" in (dl_resp.headers.get("content-disposition") or "")
        assert dl_resp.content == b"artifact-ok"


def test_artifact_download_not_found(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch, with_queue=False)

    with TestClient(app) as client:
        headers = bootstrap_headers_for_readonly(client)
        resp = client.get(
            "/tool-runs/nonexistent/artifacts/nonexistent/download",
            headers=headers,
        )
        assert resp.status_code == 404


def bootstrap_headers_for_readonly(client: TestClient) -> dict[str, str]:
    import json
    import uuid
    from datetime import datetime, timezone

    from api.auth.deps import hash_token
    from api.db import get_db_connection

    token = f"smoke-read-{uuid.uuid4()}"
    token_hash = hash_token(token)
    user_id = f"u-{uuid.uuid4()}"
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as conn:
        conn.execute("INSERT INTO users(id, name, created_at) VALUES(?,?,?)", (user_id, "read-user", now))
        conn.execute(
            """INSERT INTO api_tokens(id, user_id, token_hash, scopes, created_at)
               VALUES(?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                user_id,
                token_hash,
                json.dumps(["tool:read"]),
                now,
            ),
        )
        conn.commit()
    return {"Authorization": f"Bearer {token}"}
