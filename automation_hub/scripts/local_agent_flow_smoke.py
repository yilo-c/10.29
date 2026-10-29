"""本地代理最小闭环 smoke。

链路：
register -> heartbeat -> policy -> create run -> claim -> events -> artifact -> query events/artifacts
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from automation_hub.api.auth.deps import hash_token
from automation_hub.api.db import get_db_connection
from automation_hub.api.main import app
from worker import arq_settings as arq_settings_module


@dataclass
class SmokeFlowResult:
    node_id: str
    run_id: str
    artifact_id: str
    events_count: int
    artifacts_count: int
    step_status_codes: dict[str, int] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_user_token(scopes: list[str]) -> str:
    raw_token = f"smoke-{uuid.uuid4()}"
    token_hash = hash_token(raw_token)
    with get_db_connection() as conn:
        user_id = f"u-{uuid.uuid4()}"
        conn.execute(
            "INSERT INTO users(id, name, created_at) VALUES(?,?,?)",
            (user_id, "smoke-user", _now()),
        )
        conn.execute(
            """INSERT INTO api_tokens(id, user_id, token_hash, scopes, created_at)
               VALUES(?,?,?,?,?)""",
            (str(uuid.uuid4()), user_id, token_hash, json.dumps(scopes), _now()),
        )
        conn.commit()
    return raw_token


def _seed_tool(tool_id: str) -> None:
    now = _now()
    with get_db_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO tools(
                id, name, description, risk_level, executor, args_schema_json,
                command_json, cwd, timeout_sec, allowed_paths_json, created_at, updated_at, is_enabled
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tool_id,
                tool_id,
                "smoke tool",
                "exec_low",
                "local",
                json.dumps({"type": "object"}, ensure_ascii=False),
                json.dumps({"kind": "noop"}, ensure_ascii=False),
                ".",
                30,
                json.dumps([], ensure_ascii=False),
                now,
                now,
                1,
            ),
        )
        conn.commit()

def run_smoke_flow() -> SmokeFlowResult:
    # 避免依赖 redis，mock enqueue
    arq_settings_module.enqueue_job_sync = lambda *a, **kw: "mocked-job-id"  # type: ignore[assignment]

    user_token = _seed_user_token([
        "agent-nodes:write",
        "agent-nodes:read",
        "tool:execute",
        "tool:read",
    ])

    tool_id = f"smoke_tool_{uuid.uuid4().hex[:8]}"
    _seed_tool(tool_id)
    step_status_codes: dict[str, int] = {}

    with TestClient(app) as client:
        # 1) 注册节点
        r = client.post(
            "/agent-nodes/register",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"hostname": "smoke-host", "labels": {"env": "smoke"}, "capabilities": [tool_id]},
        )
        step_status_codes["register"] = r.status_code
        r.raise_for_status()
        reg = r.json()
        node_id = reg["node_id"]
        node_token = reg["issued_token"]

        # 2) 心跳
        r = client.post(
            f"/agent-nodes/{node_id}/heartbeat",
            headers={"Authorization": f"Bearer {node_token}"},
            json={"status": "online", "active_runs": 0, "cpu_percent": 0.1, "mem_percent": 1.2},
        )
        step_status_codes["heartbeat"] = r.status_code
        r.raise_for_status()

        # 3) 策略拉取
        r = client.get(
            f"/agent-nodes/{node_id}/policy-bundle",
            headers={"Authorization": f"Bearer {node_token}"},
        )
        step_status_codes["policy"] = r.status_code
        r.raise_for_status()

        # 4) 创建 run
        r = client.post(
            "/tool-runs",
            headers={"Authorization": f"Bearer {node_token}"},
            json={"tool_id": tool_id, "args": {"command": "echo smoke"}, "reason": "smoke"},
        )
        step_status_codes["create_run"] = r.status_code
        r.raise_for_status()
        run = r.json()
        run_id = run["run_id"]

        # 5) claim
        r = client.post(
            f"/tool-runs/{run_id}/claim",
            headers={"Authorization": f"Bearer {node_token}"},
            json={"node_id": node_id},
        )
        step_status_codes["claim"] = r.status_code
        r.raise_for_status()

        # 6) events
        r = client.post(
            f"/tool-runs/{run_id}/events",
            headers={"Authorization": f"Bearer {node_token}"},
            json={"events": [{"type": "output", "data": "hello"}, {"type": "completed", "data": "ok"}]},
        )
        step_status_codes["push_events"] = r.status_code
        r.raise_for_status()

        # 7) artifact
        r = client.post(
            f"/tool-runs/{run_id}/artifacts",
            headers={"Authorization": f"Bearer {node_token}"},
            files={"file": ("smoke.txt", b"artifact-ok", "text/plain")},
        )
        step_status_codes["upload_artifact"] = r.status_code
        r.raise_for_status()
        artifact_id = r.json()["artifact_id"]

        # 8) 查询 events/artifacts
        r = client.get(
            f"/tool-runs/{run_id}/events",
            headers={"Authorization": f"Bearer {node_token}"},
        )
        step_status_codes["list_events"] = r.status_code
        r.raise_for_status()
        events = r.json()

        r = client.get(
            f"/tool-runs/{run_id}/artifacts",
            headers={"Authorization": f"Bearer {node_token}"},
        )
        step_status_codes["list_artifacts"] = r.status_code
        r.raise_for_status()
        artifacts = r.json()

    return SmokeFlowResult(
        node_id=node_id,
        run_id=run_id,
        artifact_id=artifact_id,
        events_count=len(events),
        artifacts_count=len(artifacts),
        step_status_codes=step_status_codes,
    )


def main() -> None:
    result = run_smoke_flow()

    print("SMOKE_OK")
    print(f"node_id={result.node_id}")
    print(f"run_id={result.run_id}")
    print(f"events={result.events_count} artifacts={result.artifacts_count}")


if __name__ == "__main__":
    main()
