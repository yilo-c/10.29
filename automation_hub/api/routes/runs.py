"""模块说明：运行管理接口。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from automation_hub.api.config import settings
from automation_hub.api.auth.deps import require_scope
from automation_hub.api.tools.registry import get_tool
from automation_hub.api.routes.tool_runs import ToolRunCreate, _create_tool_run
from automation_hub.api.db import (
    create_run,
    create_script,
    get_run_by_id,
    get_runs,
    get_script_by_name,
    update_run_status,
    add_run_log,
    get_run_logs,
)
from automation_hub.api.schemas import RunCreate, RunResponse

from automation_hub.api._worker_bridge import execute_script_job, load_scripts_manifest

router = APIRouter(tags=["runs"])


def tail_text(path: str | None, max_chars: int = 2000) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:]
    except Exception:
        data = p.read_bytes()
        return data[-max_chars:].decode("utf-8", errors="replace")


def validate_script_exists(script_name: str) -> dict:
    manifest = load_scripts_manifest()
    script = next((s for s in manifest.get("scripts", []) if s.get("id") == script_name or s.get("name") == script_name), None)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    return script


@router.post("/", response_model=RunResponse)
def create_run_endpoint(payload: RunCreate, token=Depends(require_scope("tool:execute"))):
    # 兼容层：将 script_name 映射为 tool_id
    script_name = payload.script_name
    tool_id = script_name

    tool = get_tool(tool_id)
    if not tool and script_name.endswith(".py"):
        tool_id = script_name[:-3]
        tool = get_tool(tool_id)

    if not tool:
        raise HTTPException(status_code=410, detail="legacy script not registered as tool; please migrate manifest to tools")

    tool_payload = ToolRunCreate(tool_id=tool_id, args=payload.parameters or {}, reason="legacy /runs")
    tool_res = _create_tool_run(tool_payload, token, source="legacy-runs")
    if not get_script_by_name(payload.script_name):
        create_script(payload.script_name, f"compat placeholder for tool {tool_id}")
    create_run(
        run_id=tool_res.run_id,
        script_name=payload.script_name,
        parameters=json.dumps(payload.parameters or {}),
    )
    if tool_res.status != "queued":
        update_run_status(tool_res.run_id, tool_res.status)

    return RunResponse(
        run_id=tool_res.run_id,
        script_name=payload.script_name,
        parameters=json.dumps(payload.parameters or {}),
        status=tool_res.status,
        created_at=datetime.now(),
    )


@router.get("/", response_model=List[RunResponse])
def list_runs(limit: int = 50, token=Depends(require_scope("tool:read"))):
    rows = get_runs()
    limited = rows[:limit]
    res: List[RunResponse] = []
    for r in limited:
        res.append(RunResponse(
            run_id=r.get('run_id'),
            script_name=r.get('script_name'),
            parameters=r.get('parameters'),
            status=r.get('status'),
            created_at=r.get('created_at'),
            started_at=r.get('started_at'),
            completed_at=r.get('completed_at'),
            log_file_path=r.get('log_file_path'),
            result=r.get('result'),
            error_msg=r.get('error_msg'),
        ))
    return res


@router.get("/{run_id}", response_model=RunResponse)
def get_run(run_id: str, token=Depends(require_scope("tool:read"))):
    run = get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    return RunResponse(
        run_id=run.get('run_id'),
        script_name=run.get('script_name'),
        parameters=run.get('parameters'),
        status=run.get('status'),
        created_at=run.get('created_at'),
        started_at=run.get('started_at'),
        completed_at=run.get('completed_at'),
        log_file_path=run.get('log_file_path'),
        result=run.get('result'),
        error_msg=run.get('error_msg'),
    )
