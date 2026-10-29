from __future__ import annotations
import json
from datetime import datetime, timezone
from automation_hub.api.db import get_db_connection


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def upsert_tool(spec: dict) -> None:
    with get_db_connection() as conn:
        cur = conn.execute("SELECT id FROM tools WHERE id=?", (spec["id"],))
        existing = cur.fetchone()
        if existing:
            conn.execute(
                """UPDATE tools SET name=?,description=?,risk_level=?,executor=?,args_schema_json=?,
                             command_json=?,cwd=?,timeout_sec=?,allowed_paths_json=?,updated_at=?,is_enabled=?
                             WHERE id=?""",
                (spec["name"], spec["description"], spec["risk_level"], spec["executor"],
                 json.dumps(spec.get("args_schema", {}), ensure_ascii=False),
                 json.dumps(spec["command"], ensure_ascii=False), spec.get("cwd"), spec.get("timeout_sec", 120),
                 json.dumps(spec.get("allowed_paths", []), ensure_ascii=False), now_iso(), 1 if spec.get("is_enabled", True) else 0,
                 spec["id"]))
        else:
            conn.execute(
                """INSERT INTO tools(id,name,description,risk_level,executor,args_schema_json,command_json,cwd,
                                 timeout_sec,allowed_paths_json,created_at,updated_at,is_enabled)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (spec["id"], spec["name"], spec.get("description",""), spec.get("risk_level","exec_low"), spec.get("executor","docker"),
                 json.dumps(spec.get("args_schema", {}), ensure_ascii=False), json.dumps(spec["command"], ensure_ascii=False), spec.get("cwd"),
                 spec.get("timeout_sec", 120), json.dumps(spec.get("allowed_paths", []), ensure_ascii=False), now_iso(), now_iso(), 1 if spec.get("is_enabled", True) else 0))
        conn.commit()


def get_tool(tool_id: str) -> dict | None:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM tools WHERE id=?", (tool_id,)).fetchone()
        return dict(row) if row else None


def list_tools() -> list[dict]:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM tools ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def set_tool_enabled(tool_id: str, enabled: bool) -> bool:
    with get_db_connection() as conn:
        cur = conn.execute("UPDATE tools SET is_enabled=?, updated_at=? WHERE id=?", (1 if enabled else 0, now_iso(), tool_id))
        conn.commit()
        return cur.rowcount > 0
