"""模块说明：工具运行（tool_runs）接口。

提供基于 tools 表的异步执行能力：
- 创建工具运行（必要时走审批）
- 查询运行状态与输出
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Response
from pydantic import BaseModel, Field
from jsonschema import validate as js_validate
from jsonschema.exceptions import ValidationError

from automation_hub.api.auth.deps import get_current_token, require_scope
from automation_hub.api.auth.agent_node_deps import require_scope_or_agent_node
from automation_hub.api.config import settings
from automation_hub.api.db import (
    create_tool_run,
    get_tool_run_by_id,
    list_tool_runs,
    update_tool_run_status,
)
from automation_hub.api.tools.registry import get_tool
from automation_hub.api.approvals.service import create_approval
from automation_hub.api.policy.engine import decide_execute
from automation_hub.api.audit.service import log_event

from automation_hub.api._worker_bridge import enqueue_job_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tool-runs", tags=["tool-runs"])


def _safe_download_filename(filename: str) -> str:
    """限制下载文件名字符，避免响应头注入和异常字符。"""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", (filename or "artifact")).strip("._")
    return safe or "artifact"


class RunClaimRequest(BaseModel):
    node_id: str = Field(min_length=1)


class RunClaimResponse(BaseModel):
    status: str
    run_id: str | None = None
    tool_name: str | None = None
    args: Dict[str, Any] = Field(default_factory=dict)


class RunEventPayload(BaseModel):
    type: str = Field(min_length=1)
    data: str = ""


class RunEventsRequest(BaseModel):
    events: List[RunEventPayload] = Field(default_factory=list)


def tail_text(path: str | None, max_chars: int = 4000) -> str | None:
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


def _requires_approval(risk_level: str) -> bool:
    return risk_level in {"write", "exec_high"}


_PATH_ARG_KEYS = {
    "path",
    "file",
    "files",
    "dir",
    "directory",
    "cwd",
    "root",
    "source",
    "destination",
    "target",
}


def _extract_path_values(args: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    for k, v in (args or {}).items():
        if k not in _PATH_ARG_KEYS:
            continue
        if isinstance(v, str) and v.strip():
            values.append(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item.strip():
                    values.append(item)
    return values


def _normalize_allowed_prefixes(allowed: List[str]) -> List[Path]:
    # API 侧无法准确知道 worker 的工作目录，这里以“相对路径白名单”为主，尽量阻止目录穿越。
    prefixes: List[Path] = []
    for p in allowed or []:
        p = (p or "").strip()
        if not p:
            continue
        prefixes.append(Path(p))
    return prefixes


def _path_is_allowed(raw: str, allowed_prefixes: List[Path]) -> bool:
    # 若未配置 allowed_paths，则默认放行（兼容旧工具）；推荐工具明确配置 allowed_paths。
    if not allowed_prefixes:
        return True

    candidate = Path(raw)
    # 绝对路径：必须落在某个绝对 allowed 前缀下
    if candidate.is_absolute():
        for pref in allowed_prefixes:
            if pref.is_absolute() and (candidate == pref or pref in candidate.parents):
                return True
        return False

    # 相对路径：禁止目录穿越，并要求以某个 allowed 前缀开头
    parts = candidate.parts
    if any(part == ".." for part in parts):
        return False

    for pref in allowed_prefixes:
        if pref == Path("."):
            return True
        try:
            # 例如 pref='tests'，candidate='tests/a.py'
            rel = candidate
            if rel == pref or pref in rel.parents:
                return True
        except Exception:
            continue
    return False


def _validate_args_and_paths(tool: dict, args: Dict[str, Any]) -> None:
    schema_raw = tool.get("args_schema_json")
    if schema_raw:
        try:
            schema = json.loads(schema_raw)
            js_validate(instance=args or {}, schema=schema)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=f"args_schema 校验失败: {e.message}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"args_schema 解析/校验异常: {str(e)}")

    allowed_raw = tool.get("allowed_paths_json") or "[]"
    try:
        allowed_list = json.loads(allowed_raw)
        if not isinstance(allowed_list, list):
            allowed_list = []
    except Exception:
        allowed_list = []

    allowed_prefixes = _normalize_allowed_prefixes([str(x) for x in allowed_list if isinstance(x, (str, int, float))])
    for pv in _extract_path_values(args or {}):
        if not _path_is_allowed(pv, allowed_prefixes):
            raise HTTPException(status_code=403, detail=f"路径不在 allowed_paths 白名单内: {pv}")


class ToolRunCreate(BaseModel):
    tool_id: str = Field(min_length=1)
    args: Dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", description="审批原因（可选）")


class ToolRunRetryRequest(BaseModel):
    reason: str = Field(default="", description="重发原因（可选）")


class ToolRunResponse(BaseModel):
    run_id: str
    tool_id: str
    status: str
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    approval_id: Optional[str] = None
    stdout_tail: Optional[str] = None
    stderr_tail: Optional[str] = None
    error_msg: Optional[str] = None


def _tool_run_response_from_row(r: dict, *, fallback_tool_id: str | None = None, fallback_status: str | None = None) -> ToolRunResponse:
    return ToolRunResponse(
        run_id=r.get("id") or r.get("run_id"),
        tool_id=r.get("tool_id") or fallback_tool_id,
        status=r.get("status") or fallback_status or "queued",
        created_at=r.get("created_at"),
        started_at=r.get("started_at"),
        finished_at=r.get("finished_at"),
        exit_code=r.get("exit_code"),
        approval_id=r.get("approval_request_id"),
        stdout_tail=tail_text(r.get("stdout_path")),
        stderr_tail=tail_text(r.get("stderr_path")),
        error_msg=r.get("error_msg"),
    )


def _create_tool_run(payload: ToolRunCreate, token: dict, source: str = "tool-runs") -> ToolRunResponse:
    tool = get_tool(payload.tool_id)
    if not tool or not int(tool.get("is_enabled", 1)):
        raise HTTPException(status_code=404, detail="tool not found")

    _validate_args_and_paths(tool, payload.args)

    run_id = str(uuid.uuid4())

    run_dir = Path(settings.RUNS_DIR) / "tool_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = str(run_dir / "stdout.txt")
    stderr_path = str(run_dir / "stderr.txt")

    risk_level = tool.get("risk_level", "exec_low")
    user_id = token.get("user_id")

    # 统一策略判定（权限/风险/Schema）
    try:
        scopes = json.loads(token.get("scopes") or "[]")
    except Exception:
        scopes = []
    decision = decide_execute(scopes=scopes, tool=tool, args=payload.args or {})
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)

    approval_id: str | None = None
    status = "queued"

    if decision.requires_approval:
        status = "pending_approval"
        approval_id = create_approval(
            user_id=user_id,
            device_id=token.get("device_id"),
            resource_type="run",
            resource_id=run_id,
            action="execute",
            risk_level=risk_level,
            reason=payload.reason or f"execute tool {payload.tool_id}",
            payload={"tool_id": payload.tool_id, "args": payload.args, "run_id": run_id, "source": source},
        )

    ok = create_tool_run(
        run_id=run_id,
        tool_id=payload.tool_id,
        args=payload.args,
        status=status,
        created_by_user_id=user_id,
        approval_request_id=approval_id,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="failed to create tool run")

    log_event(
        event_type="run.requested",
        action="execute",
        status="success",
        actor_user_id=user_id,
        actor_device_id=token.get("device_id"),
        resource_type="run",
        resource_id=run_id,
        message="tool run requested",
        meta={"tool_id": payload.tool_id, "risk_level": risk_level, "requires_approval": decision.requires_approval, "source": source},
    )

    if status == "queued":
        timeout = int(tool.get("timeout_sec") or 120) + 30
        job_id = enqueue_job_sync(
            "arq_run_tool_job",
            run_id,
            payload.tool_id,
            payload.args,
            user_id,
            job_timeout=timeout,
        )
        # P0-4：入队失败时把 run 置为 failed，避免永久卡 queued
        if job_id is None:
            update_tool_run_status(
                run_id=run_id,
                status="failed",
                finished_at=datetime.now(timezone.utc).isoformat(),
                error_msg="enqueue failed: ARQ/Redis unavailable",
            )
            logger.error("enqueue failed for run %s, marked as failed", run_id)

    row = get_tool_run_by_id(run_id) or {}
    return _tool_run_response_from_row(
        row,
        fallback_tool_id=payload.tool_id,
        fallback_status=status,
    )


@router.post("", response_model=ToolRunResponse)
def create_tool_run_endpoint(payload: ToolRunCreate, token=Depends(require_scope_or_agent_node("tool:execute"))):
    return _create_tool_run(payload, token, source="tool-runs")


@router.post("/{run_id}/retry", response_model=ToolRunResponse)
def retry_tool_run_endpoint(run_id: str, payload: ToolRunRetryRequest, token=Depends(require_scope("tool:execute"))):
    original = get_tool_run_by_id(run_id)
    if not original:
        raise HTTPException(status_code=404, detail="run not found")

    original_status = original.get("status")
    if original_status in {"queued", "running", "pending_approval"}:
        raise HTTPException(status_code=409, detail="run is not in a retryable terminal state")

    try:
        args = json.loads(original.get("args_json") or "{}")
        if not isinstance(args, dict):
            args = {}
    except Exception:
        args = {}

    reason = (payload.reason or "").strip() or f"retry run {run_id}"
    retried = _create_tool_run(
        ToolRunCreate(tool_id=original["tool_id"], args=args, reason=reason),
        token,
        source="tool-runs-retry",
    )

    log_event(
        event_type="run.retried",
        action="retry",
        status="success",
        actor_user_id=token.get("user_id"),
        actor_device_id=token.get("device_id"),
        resource_type="run",
        resource_id=retried.run_id,
        message="tool run retried",
        meta={
            "retried_from_run_id": run_id,
            "previous_status": original_status,
            "tool_id": original.get("tool_id"),
            "reason": reason,
        },
    )
    return retried


@router.get("", response_model=List[ToolRunResponse])
def list_tool_runs_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    token=Depends(get_current_token),
):
    rows = list_tool_runs(limit=limit)
    res: List[ToolRunResponse] = []
    for r in rows:
        res.append(_tool_run_response_from_row(r))
    return res


@router.get("/{run_id}", response_model=ToolRunResponse)
def get_tool_run_endpoint(run_id: str, token=Depends(get_current_token)):
    r = get_tool_run_by_id(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run not found")

    return _tool_run_response_from_row(r)


@router.post("/{run_id}/claim", response_model=RunClaimResponse)
def claim_tool_run_endpoint(run_id: str, payload: RunClaimRequest, token=Depends(require_scope_or_agent_node("tool:execute"))):
    if token.get("actor_type") == "node" and token.get("id") != payload.node_id:
        raise HTTPException(status_code=403, detail="node token does not match request node_id")

    row = get_tool_run_by_id(run_id)
    if not row:
        return RunClaimResponse(status="not_found")

    if row.get("status") not in {"queued", "running"}:
        return RunClaimResponse(status="already_claimed", run_id=run_id)

    started_at = datetime.utcnow().isoformat() + "Z"

    # 兼容不同数据库版本：claimed_by 列不存在时退化为仅置 running。
    try:
        from api.db import get_db_connection

        with get_db_connection() as conn:
            conn.execute(
                "UPDATE tool_runs SET status='running', started_at=?, claimed_by=? WHERE id=?",
                (started_at, payload.node_id, run_id),
            )
            conn.commit()
    except Exception:
        update_tool_run_status(run_id=run_id, status="running", started_at=started_at)

    latest = get_tool_run_by_id(run_id) or row
    try:
        args = json.loads(latest.get("args_json") or "{}")
        if not isinstance(args, dict):
            args = {}
    except Exception:
        args = {}

    tool_name = latest.get("tool_name") or latest.get("tool_id")
    return RunClaimResponse(status="claimed", run_id=run_id, tool_name=tool_name, args=args)


@router.post("/{run_id}/events")
def push_tool_run_events_endpoint(run_id: str, payload: RunEventsRequest, token=Depends(require_scope_or_agent_node("tool:execute"))):
    row = get_tool_run_by_id(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")

    now = datetime.utcnow().isoformat() + "Z"
    from api.db import get_db_connection

    terminal_status = None
    with get_db_connection() as conn:
        for evt in payload.events:
            conn.execute(
                "INSERT INTO run_events(id, run_id, event_type, data, created_at) VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), run_id, evt.type, evt.data, now),
            )
            if evt.type == "completed":
                terminal_status = "succeeded"
            elif evt.type == "failed":
                terminal_status = "failed"

        conn.commit()

    if terminal_status:
        update_tool_run_status(run_id=run_id, status=terminal_status, finished_at=now)

    return {"accepted": len(payload.events)}


@router.post("/{run_id}/artifacts")
async def upload_tool_run_artifact_endpoint(
    run_id: str,
    file: UploadFile = File(...),
    token=Depends(require_scope_or_agent_node("tool:execute")),
):
    row = get_tool_run_by_id(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")

    content = await file.read()
    now = datetime.utcnow().isoformat() + "Z"
    artifact_id = str(uuid.uuid4())

    from api.db import get_db_connection

    with get_db_connection() as conn:
        conn.execute(
            """INSERT INTO run_artifacts
               (id, run_id, filename, size_bytes, content_type, data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (artifact_id, run_id, file.filename, len(content), file.content_type, content, now),
        )
        conn.commit()

    return {"artifact_id": artifact_id, "size": len(content)}


@router.get("/{run_id}/events")
def list_tool_run_events_endpoint(run_id: str, token=Depends(require_scope_or_agent_node("tool:read"))):
    row = get_tool_run_by_id(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")

    from api.db import get_db_connection

    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT id, run_id, event_type, data, created_at FROM run_events WHERE run_id=? ORDER BY created_at ASC",
            (run_id,),
        ).fetchall()

    return [dict(r) for r in rows]


@router.get("/{run_id}/artifacts")
def list_tool_run_artifacts_endpoint(run_id: str, token=Depends(require_scope_or_agent_node("tool:read"))):
    row = get_tool_run_by_id(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")

    from api.db import get_db_connection

    with get_db_connection() as conn:
        rows = conn.execute(
            """SELECT id, run_id, filename, size_bytes, content_type, created_at
               FROM run_artifacts WHERE run_id=? ORDER BY created_at ASC""",
            (run_id,),
        ).fetchall()

    return [dict(r) for r in rows]


@router.get("/{run_id}/artifacts/{artifact_id}")
def get_tool_run_artifact_endpoint(run_id: str, artifact_id: str, token=Depends(require_scope_or_agent_node("tool:read"))):
    from api.db import get_db_connection

    with get_db_connection() as conn:
        row = conn.execute(
            """SELECT id, run_id, filename, size_bytes, content_type, data, created_at
               FROM run_artifacts WHERE id=? AND run_id=?""",
            (artifact_id, run_id),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="artifact not found")

    result = dict(row)
    result.pop("data", None)
    return result


@router.get("/{run_id}/artifacts/{artifact_id}/download")
def download_tool_run_artifact_endpoint(
    run_id: str,
    artifact_id: str,
    disposition: str = Query(default="attachment", pattern="^(attachment|inline)$"),
    token=Depends(require_scope_or_agent_node("tool:read")),
):
    from api.db import get_db_connection

    with get_db_connection() as conn:
        row = conn.execute(
            """SELECT id, run_id, filename, size_bytes, content_type, data
               FROM run_artifacts WHERE id=? AND run_id=?""",
            (artifact_id, run_id),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="artifact not found")

    content = row["data"] or b""
    if not isinstance(content, (bytes, bytearray)):
        content = bytes(content)

    content_type = row["content_type"] or "application/octet-stream"
    filename = _safe_download_filename(row["filename"])
    headers = {
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "X-Artifact-Id": row["id"],
    }

    return Response(content=bytes(content), media_type=content_type, headers=headers)
