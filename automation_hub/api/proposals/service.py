"""提案服务逻辑。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from automation_hub.api.db import get_db_connection


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_proposal(
    title: str,
    summary: str,
    plan_md: str,
    patch_diff: str | None,
    verify_tools: list[dict],
    risk_level: str,
    created_by_user_id: str,
    target_repo_id: str | None = None,
) -> str:
    pid = str(uuid.uuid4())
    with get_db_connection() as conn:
        conn.execute(
            """INSERT INTO proposals(
                   id, title, summary, plan_md, patch_diff, verify_commands_json,
                   risk_level, status, target_repo_id, created_by_user_id, created_at, updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                title,
                summary,
                plan_md,
                patch_diff,
                json.dumps(verify_tools or [], ensure_ascii=False),
                risk_level,
                "draft",
                target_repo_id,
                created_by_user_id,
                now_iso(),
                now_iso(),
            ),
        )
        conn.commit()
    return pid


def list_proposals(limit: int = 50) -> List[dict]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM proposals ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]


def get_proposal(pid: str) -> Optional[dict]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None


def update_proposal_status(pid: str, status: str) -> None:
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE proposals SET status=?, updated_at=? WHERE id=?",
            (status, now_iso(), pid),
        )
        conn.commit()


def mark_applied(pid: str) -> None:
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE proposals SET status='applied', applied_at=?, updated_at=? WHERE id=?",
            (now_iso(), now_iso(), pid),
        )
        conn.commit()


def mark_rejected(pid: str) -> None:
    update_proposal_status(pid, "rejected")
