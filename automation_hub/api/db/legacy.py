# -*- coding: utf-8 -*-
"""旧表 CRUD（scripts / tasks / runs / tool_runs）。

已从原生 sqlite3 迁移到 SQLAlchemy 2.0 ORM。
保持与旧代码相同的函数签名和返回格式。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from .session import get_db_session, now_iso
from ..models.legacy import Script, Task, Run, RunLog
from ..models.tools import ToolRun
from sqlalchemy import select


# ==================== Scripts ====================

def get_scripts() -> List[Dict[str, Any]]:
    with get_db_session(autocommit=False) as session:
        stmt = select(Script).order_by(Script.created_at.desc())
        rows = session.execute(stmt).scalars().all()
        result = [
            {"id": r.id, "name": r.name, "description": r.description, "created_at": r.created_at}
            for r in rows
        ]
    return result


def get_script_by_name(name: str) -> Optional[Dict[str, Any]]:
    with get_db_session(autocommit=False) as session:
        row = session.execute(
            select(Script).where(Script.name == name)
        ).scalar_one_or_none()
        if row is None:
            return None
        result = {"id": row.id, "name": row.name, "description": row.description, "created_at": row.created_at}
    return result


def create_script(name: str, description: str) -> bool:
    try:
        with get_db_session() as session:
            script = Script(name=name, description=description)
            session.add(script)
        return True
    except Exception:
        return False


def update_script(name: str, description: str) -> bool:
    with get_db_session() as session:
        row = session.execute(
            select(Script).where(Script.name == name)
        ).scalar_one_or_none()
        if row is None:
            return False
        row.description = description
    return True


def delete_script(name: str) -> bool:
    with get_db_session(autocommit=False) as session:
        row = session.execute(
            select(Script).where(Script.name == name)
        ).scalar_one_or_none()
        if row is None:
            session.commit()
            return False
        session.delete(row)
        session.commit()
    return True


# ==================== Tasks ====================

def get_tasks() -> List[Dict[str, Any]]:
    with get_db_session(autocommit=False) as session:
        stmt = select(Task).order_by(Task.created_at.desc())
        rows = session.execute(stmt).scalars().all()
        result = [
            {
                "id": r.id, "name": r.name, "script_name": r.script_name,
                "parameters": r.parameters, "status": r.status,
                "created_at": r.created_at, "scheduled_time": r.scheduled_time,
                "completed_at": r.completed_at,
            }
            for r in rows
        ]
    return result


def get_task_by_id(task_id: int) -> Optional[Dict[str, Any]]:
    with get_db_session(autocommit=False) as session:
        row = session.get(Task, task_id)
        if row is None:
            return None
        result = {
            "id": row.id, "name": row.name, "script_name": row.script_name,
            "parameters": row.parameters, "status": row.status,
            "created_at": row.created_at, "scheduled_time": row.scheduled_time,
            "completed_at": row.completed_at,
        }
    return result


def create_task(name: str, script_name: str, parameters: str = None, scheduled_time=None) -> int:
    with get_db_session() as session:
        task = Task(
            name=name, script_name=script_name,
            parameters=parameters, scheduled_time=scheduled_time,
            status="pending",
        )
        session.add(task)
        session.flush()
        new_id = task.id
    return new_id


def update_task_status(task_id: int, status: str):
    with get_db_session() as session:
        row = session.get(Task, task_id)
        if row is None:
            return
        row.status = status
        if status in ("completed", "failed"):
            from datetime import datetime, timezone
            row.completed_at = datetime.now(timezone.utc).isoformat()


def delete_task(task_id: int) -> bool:
    with get_db_session(autocommit=False) as session:
        row = session.get(Task, task_id)
        if row is None:
            session.commit()
            return False
        session.delete(row)
        session.commit()
    return True


# ==================== Runs ====================

def get_runs() -> List[Dict[str, Any]]:
    with get_db_session(autocommit=False) as session:
        stmt = select(Run).order_by(Run.created_at.desc())
        rows = session.execute(stmt).scalars().all()
        result = [
            {
                "id": r.id, "run_id": r.run_id, "script_name": r.script_name,
                "parameters": r.parameters, "status": r.status,
                "created_at": r.created_at, "started_at": r.started_at,
                "completed_at": r.completed_at, "log_file_path": r.log_file_path,
                "result": r.result, "error_msg": r.error_msg,
            }
            for r in rows
        ]
    return result


def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    return get_runs()[: int(limit)]


def get_run_by_id(run_id: str) -> Optional[Dict[str, Any]]:
    with get_db_session(autocommit=False) as session:
        row = session.execute(
            select(Run).where(Run.run_id == run_id)
        ).scalar_one_or_none()
        if row is None:
            return None
        result = {
            "id": row.id, "run_id": row.run_id, "script_name": row.script_name,
            "parameters": row.parameters, "status": row.status,
            "created_at": row.created_at, "started_at": row.started_at,
            "completed_at": row.completed_at, "log_file_path": row.log_file_path,
            "result": row.result, "error_msg": row.error_msg,
        }
    return result


def create_run(run_id: str, script_name: str, parameters: str = None) -> bool:
    try:
        with get_db_session() as session:
            run = Run(
                run_id=run_id, script_name=script_name,
                parameters=parameters, status="queued",
            )
            session.add(run)
        return True
    except Exception:
        return False


def add_run_log(run_id: str, message: str) -> None:
    with get_db_session() as session:
        log = RunLog(run_id=run_id, message=message)
        session.add(log)


def get_run_logs(run_id: str) -> List[Dict[str, Any]]:
    with get_db_session(autocommit=False) as session:
        stmt = (
            select(RunLog)
            .where(RunLog.run_id == run_id)
            .order_by(RunLog.created_at.asc())
        )
        rows = session.execute(stmt).scalars().all()
        result = [{"id": r.id, "run_id": r.run_id, "message": r.message, "created_at": r.created_at} for r in rows]
    return result


def update_run_status(
    run_id: str,
    status: str,
    started: bool = False,
    completed: bool = False,
    result: str = None,
    error_msg: str = None,
):
    with get_db_session() as session:
        row = session.execute(
            select(Run).where(Run.run_id == run_id)
        ).scalar_one_or_none()
        if row is None:
            return
        from datetime import datetime, timezone
        row.status = status
        if started:
            row.started_at = datetime.now(timezone.utc).isoformat()
        if completed:
            row.completed_at = datetime.now(timezone.utc).isoformat()
        if result is not None:
            row.result = result
        if error_msg is not None:
            row.error_msg = error_msg


# ==================== Tool Runs ====================

def create_tool_run(
    run_id: str,
    tool_id: str,
    args: dict,
    status: str,
    created_by_user_id: str | None = None,
    approval_request_id: str | None = None,
    stdout_path: str | None = None,
    stderr_path: str | None = None,
) -> bool:
    try:
        with get_db_session() as session:
            tr = ToolRun(
                id=run_id,
                tool_id=tool_id,
                args_json=json.dumps(args or {}, ensure_ascii=False),
                status=status,
                created_at=now_iso(),
                updated_at=now_iso(),
                created_by_user_id=created_by_user_id,
                approval_request_id=approval_request_id,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
            session.add(tr)
        return True
    except Exception:
        return False


def get_tool_run_by_id(run_id: str) -> Optional[Dict[str, Any]]:
    with get_db_session(autocommit=False) as session:
        row = session.get(ToolRun, run_id)
        if row is None:
            return None
        result = {
            "id": row.id, "tool_id": row.tool_id, "tool_name": row.tool_name,
            "args_json": row.args_json, "status": row.status,
            "created_at": row.created_at, "started_at": row.started_at,
            "finished_at": row.finished_at, "completed_at": row.completed_at,
            "exit_code": row.exit_code, "result_json": row.result_json,
            "error_msg": row.error_msg, "stdout_path": row.stdout_path,
            "stderr_path": row.stderr_path,
            "created_by_user_id": row.created_by_user_id,
            "approval_request_id": row.approval_request_id,
            "claimed_by": row.claimed_by,
        }
    return result


def list_tool_runs(limit: int = 50) -> List[Dict[str, Any]]:
    with get_db_session(autocommit=False) as session:
        stmt = (
            select(ToolRun)
            .order_by(ToolRun.created_at.desc())
            .limit(int(limit))
        )
        rows = session.execute(stmt).scalars().all()
        result = [
            {
                "id": r.id, "tool_id": r.tool_id, "tool_name": r.tool_name,
                "args_json": r.args_json, "status": r.status,
                "created_at": r.created_at, "started_at": r.started_at,
                "finished_at": r.finished_at, "completed_at": r.completed_at,
                "exit_code": r.exit_code, "result_json": r.result_json,
                "error_msg": r.error_msg, "stdout_path": r.stdout_path,
                "stderr_path": r.stderr_path,
                "created_by_user_id": r.created_by_user_id,
                "approval_request_id": r.approval_request_id,
                "claimed_by": r.claimed_by,
            }
            for r in rows
        ]
    return result


def update_tool_run_status(
    run_id: str,
    status: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    exit_code: int | None = None,
    result: dict | None = None,
    error_msg: str | None = None,
) -> None:
    with get_db_session() as session:
        row = session.get(ToolRun, run_id)
        if row is None:
            return
        row.status = status
        if started_at is not None:
            row.started_at = started_at
        if finished_at is not None:
            row.finished_at = finished_at
        if exit_code is not None:
            row.exit_code = exit_code
        if result is not None:
            row.result_json = json.dumps(result, ensure_ascii=False)
        if error_msg is not None:
            row.error_msg = error_msg


def set_tool_run_approval(run_id: str, approval_request_id: str | None) -> None:
    with get_db_session() as session:
        row = session.get(ToolRun, run_id)
        if row is None:
            return
        row.approval_request_id = approval_request_id
