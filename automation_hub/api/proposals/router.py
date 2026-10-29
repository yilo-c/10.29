"""提案路由（最小版）。"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from automation_hub.api.auth.deps import require_scope
from automation_hub.api.audit.service import log_event
from automation_hub.api.config import settings
from automation_hub.api.approvals.service import create_approval
from automation_hub.api.proposals.models import (
    ProposalCreate,
    ProposalRepoContextSnapshot,
    ProposalResponse,
    VerifyToolRef,
)
from automation_hub.api.repos.router import _search_repo_or_raise
from automation_hub.api.proposals.service import (
    create_proposal,
    get_proposal,
    list_proposals,
    mark_applied,
    mark_rejected,
    update_proposal_status,
)
from automation_hub.api.db import create_tool_run, get_tool_run_by_id
from automation_hub.api.tools.registry import get_tool
from automation_hub.api._worker_bridge import run_tool_job, is_proposal_approved

router = APIRouter(prefix="/proposals", tags=["proposals"])


class ProposalRetryRequest(BaseModel):
    reason: str = Field(default="", description="重提交通知或上下文（可选）")


def _proposal_dir(proposal_id: str) -> Path:
    return Path(settings.DATA_DIR) / "proposals" / proposal_id


def _repo_context_file(proposal_id: str) -> Path:
    return _proposal_dir(proposal_id) / "repo_context.json"


def _save_repo_context_snapshot(proposal_id: str, snapshot: dict) -> None:
    proposal_dir = _proposal_dir(proposal_id)
    proposal_dir.mkdir(parents=True, exist_ok=True)
    _repo_context_file(proposal_id).write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_repo_context_snapshot(proposal_id: str) -> ProposalRepoContextSnapshot | None:
    snapshot_path = _repo_context_file(proposal_id)
    if not snapshot_path.exists():
        return None
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return ProposalRepoContextSnapshot(**payload)


def _build_repo_context_snapshot(repo: dict, search_payload) -> ProposalRepoContextSnapshot:
    items = [item.model_dump() if hasattr(item, "model_dump") else item for item in search_payload.items]
    injected_files = [item["path"] for item in items]
    return ProposalRepoContextSnapshot(
        repo_id=repo.get("id"),
        repo_name=repo.get("name"),
        query=search_payload.query,
        engine=search_payload.engine,
        total=search_payload.total,
        injected_files=injected_files,
        items=items,
    )


def _proposal_to_response(p: dict) -> ProposalResponse:
    verify_commands: list[str] = []
    verify_tools: list[VerifyToolRef] = []
    try:
        raw = json.loads(p.get("verify_commands_json") or "[]")
        if isinstance(raw, list) and raw and isinstance(raw[0], dict):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                tool_id = str(item.get("tool_id") or "").strip()
                if not tool_id:
                    continue
                args = item.get("args")
                if not isinstance(args, dict):
                    args = {}
                verify_tools.append(VerifyToolRef(tool_id=tool_id, args=args))
        elif isinstance(raw, list):
            verify_commands = [str(x) for x in raw if x is not None]
    except Exception:
        verify_commands = []
        verify_tools = []

    return ProposalResponse(
        id=p["id"],
        title=p["title"],
        summary=p["summary"],
        plan_md=p["plan_md"],
        patch_diff=p.get("patch_diff"),
        verify_tools=verify_tools,
        verify_commands=verify_commands,
        risk_level=p.get("risk_level", "write"),
        status=p.get("status", "draft"),
        created_at=p.get("created_at"),
        updated_at=p.get("updated_at"),
        applied_at=p.get("applied_at"),
        repo_context=_load_repo_context_snapshot(p["id"]),
    )


@router.post("", response_model=ProposalResponse)
def create(payload: ProposalCreate, token=Depends(require_scope("proposal:write"))):
    if payload.verify_commands:
        raise HTTPException(
            status_code=400,
            detail="verify_commands (string commands) is not allowed; use verify_tools (tool_id-based)",
        )

    repo_context_snapshot: ProposalRepoContextSnapshot | None = None
    target_repo_id: str | None = None
    if payload.repo_context:
        scopes = token.get("scopes_list") or []
        if "repo:read" not in scopes:
            raise HTTPException(status_code=403, detail="missing scope: repo:read")
        repo, search_payload = _search_repo_or_raise(
            payload.repo_context.repo_id,
            payload.repo_context.query,
            payload.repo_context.limit,
            payload.repo_context.offset,
        )
        repo_context_snapshot = _build_repo_context_snapshot(repo, search_payload)
        target_repo_id = repo.get("id")

    pid = create_proposal(
        title=payload.title,
        summary=payload.summary,
        plan_md=payload.plan_md,
        patch_diff=payload.patch_diff,
        verify_tools=[vt.model_dump() for vt in (payload.verify_tools or [])],
        risk_level=payload.risk_level,
        created_by_user_id=token.get("user_id"),
        target_repo_id=target_repo_id,
    )

    if repo_context_snapshot is not None:
        _save_repo_context_snapshot(pid, repo_context_snapshot.model_dump())

    log_event(
        event_type="proposal.created",
        action="create",
        status="success",
        actor_user_id=token.get("user_id"),
        actor_device_id=token.get("device_id"),
        resource_type="proposal",
        resource_id=pid,
        message="proposal created",
        meta={
            "target_repo_id": target_repo_id,
            "repo_context": repo_context_snapshot.model_dump() if repo_context_snapshot is not None else None,
        },
    )

    p = get_proposal(pid) or {}
    return _proposal_to_response(p)


@router.get("", response_model=List[ProposalResponse])
def list_all(limit: int = 50, token=Depends(require_scope("proposal:read"))):
    rows = list_proposals(limit=limit)
    return [_proposal_to_response(r) for r in rows]


@router.get("/{proposal_id}", response_model=ProposalResponse)
def get_one(proposal_id: str, token=Depends(require_scope("proposal:read"))):
    p = get_proposal(proposal_id)
    if not p:
        raise HTTPException(status_code=404, detail="proposal not found")
    return _proposal_to_response(p)


def _submit_proposal(
    proposal_id: str,
    token: dict,
    *,
    action: str,
    required_statuses: set[str],
    reason: str,
):
    p = get_proposal(proposal_id)
    if not p:
        raise HTTPException(status_code=404, detail="proposal not found")

    current_status = p.get("status")
    if current_status not in required_statuses:
        expected = "/".join(sorted(required_statuses))
        raise HTTPException(status_code=409, detail=f"proposal not in {expected} state")

    update_proposal_status(proposal_id, "pending_approval")

    approval_id = create_approval(
        user_id=token.get("user_id"),
        device_id=token.get("device_id"),
        resource_type="proposal",
        resource_id=proposal_id,
        action="apply",
        risk_level=p.get("risk_level", "write"),
        reason=reason,
        payload={"proposal_id": proposal_id, "title": p.get("title"), "source_status": current_status, "submit_action": action},
    )

    log_event(
        event_type="proposal.retried" if action == "retry" else "proposal.submitted",
        action=action,
        status="success",
        actor_user_id=token.get("user_id"),
        actor_device_id=token.get("device_id"),
        resource_type="proposal",
        resource_id=proposal_id,
        message="proposal retried" if action == "retry" else "proposal submitted",
        meta={"approval_id": approval_id, "from_status": current_status, "reason": reason},
    )

    return {"submitted": True, "approval_id": approval_id, "action": action}


@router.post("/{proposal_id}/submit")
def submit(proposal_id: str, token=Depends(require_scope("proposal:write"))):
    return _submit_proposal(
        proposal_id,
        token,
        action="submit",
        required_statuses={"draft", "rejected"},
        reason="submit proposal",
    )


@router.post("/{proposal_id}/retry")
def retry(proposal_id: str, payload: ProposalRetryRequest, token=Depends(require_scope("proposal:write"))):
    retry_reason = (payload.reason or "").strip() or "retry proposal"
    return _submit_proposal(
        proposal_id,
        token,
        action="retry",
        required_statuses={"rejected"},
        reason=retry_reason,
    )


def _apply_patch_or_raise(patch_text: str, workdir: Path, patch_dir: Path) -> Path:
    patch_dir.mkdir(parents=True, exist_ok=True)
    patch_path = patch_dir / "proposal.patch"
    patch_path.write_text(patch_text, encoding="utf-8")

    check = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        cwd=str(workdir),
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        raise HTTPException(status_code=400, detail=f"patch check failed: {check.stderr.strip()}")

    apply = subprocess.run(
        ["git", "apply", str(patch_path)],
        cwd=str(workdir),
        capture_output=True,
        text=True,
    )
    if apply.returncode != 0:
        raise HTTPException(status_code=500, detail=f"patch apply failed: {apply.stderr.strip()}")

    return patch_path


def _tail_text(path: str | None, limit: int = 2000) -> str:
    if not path:
        return ""
    try:
        p = Path(path)
        if not p.exists():
            return ""
        txt = p.read_text(encoding="utf-8", errors="ignore")
        return txt[-limit:]
    except Exception:
        return ""


def _run_verify_tools(verify_tools: list[VerifyToolRef], proposal_dir: Path, actor_user_id: str | None) -> list[dict]:
    results: list[dict] = []

    for vt in verify_tools or []:
        tool_id = (vt.tool_id or "").strip()
        if not tool_id:
            continue

        tool = get_tool(tool_id)
        if not tool or not int(tool.get("is_enabled", 1)):
            raise HTTPException(status_code=400, detail=f"verify tool not found/enabled: {tool_id}")

        risk = (tool.get("risk_level") or "").strip()
        if risk not in {"read", "exec_low"}:
            raise HTTPException(status_code=400, detail=f"verify tool risk_level not allowed: {tool_id} ({risk})")

        run_id = str(uuid.uuid4())
        run_dir = proposal_dir / "verify_runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = str(run_dir / "stdout.txt")
        stderr_path = str(run_dir / "stderr.txt")

        ok = create_tool_run(
            run_id=run_id,
            tool_id=tool_id,
            args=vt.args or {},
            status="queued",
            created_by_user_id=actor_user_id,
            approval_request_id=None,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        if not ok:
            raise HTTPException(status_code=500, detail=f"failed to create verify tool run: {tool_id}")

        run_tool_job(run_id=run_id, tool_id=tool_id, args=vt.args or {}, user_id=actor_user_id)

        r = get_tool_run_by_id(run_id) or {}
        rc = r.get("exit_code")
        status = r.get("status")
        results.append(
            {
                "tool_id": tool_id,
                "run_id": run_id,
                "status": status,
                "exit_code": rc,
                "stdout": _tail_text(r.get("stdout_path")),
                "stderr": _tail_text(r.get("stderr_path")),
            }
        )

        if status != "succeeded":
            raise HTTPException(status_code=400, detail=f"verify failed: {tool_id} ({status})")

    return results


def _rollback_patch(patch_path: Path, workdir: Path) -> None:
    if patch_path.exists():
        subprocess.run(["git", "apply", "-R", str(patch_path)], cwd=str(workdir))
    else:
        subprocess.run(["git", "restore", "--staged", "--worktree", "."], cwd=str(workdir))


def _git_head(workdir: Path) -> str | None:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(workdir), capture_output=True, text=True)
        if res.returncode == 0:
            return (res.stdout or "").strip() or None
    except Exception:
        return None
    return None


@router.post("/{proposal_id}/apply")
def apply(proposal_id: str, token=Depends(require_scope("proposal:write"))):
    p = get_proposal(proposal_id)
    if not p:
        raise HTTPException(status_code=404, detail="proposal not found")

    if not is_proposal_approved(proposal_id):
        raise HTTPException(status_code=403, detail="proposal not approved")

    patch_text = p.get("patch_diff") or ""
    if not patch_text.strip():
        raise HTTPException(status_code=400, detail="proposal has no patch_diff")

    workdir = Path(settings.WORKSPACE_DIR)
    proposal_dir = Path(settings.DATA_DIR) / "proposals" / proposal_id
    proposal_dir.mkdir(parents=True, exist_ok=True)

    patch_path = None
    verify_results: list[dict] = []
    before_head = _git_head(workdir)
    try:
        patch_path = _apply_patch_or_raise(patch_text, workdir, proposal_dir)
        # 将 patch 保存一份到持久目录（用于回滚/追溯）
        (proposal_dir / "patch.applied.diff").write_text(patch_text, encoding="utf-8")

        # P0：verify 仅允许 tool_id 白名单执行（不允许任意 shell 字符串命令）
        verify_tools_raw = json.loads(p.get("verify_commands_json") or "[]")
        verify_tools: list[VerifyToolRef] = []
        if isinstance(verify_tools_raw, list) and verify_tools_raw and isinstance(verify_tools_raw[0], dict):
            for item in verify_tools_raw:
                if not isinstance(item, dict):
                    continue
                tid = str(item.get("tool_id") or "").strip()
                if not tid:
                    continue
                args = item.get("args")
                if not isinstance(args, dict):
                    args = {}
                verify_tools.append(VerifyToolRef(tool_id=tid, args=args))
        elif isinstance(verify_tools_raw, list) and verify_tools_raw:
            # 旧格式：字符串命令列表 —— 不再允许执行
            raise HTTPException(
                status_code=400,
                detail="proposal verify_commands (string commands) is no longer supported; migrate to verify_tools",
            )

        from contextlib import contextmanager

        @contextmanager
        def _env_for_worker():
            # 让 run_tool_job 的路径/白名单检查能在本地开发与容器内都尽量一致
            overrides = {
                "WORKSPACE": str(settings.WORKSPACE_DIR),
                "DATA_DIR": str(settings.DATA_DIR),
            }
            old = {k: os.environ.get(k) for k in overrides.keys()}
            os.environ.update(overrides)
            try:
                yield
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

        with _env_for_worker():
            verify_results = _run_verify_tools(verify_tools, proposal_dir, token.get("user_id"))

        after_head = _git_head(workdir)
        apply_meta = {
            "proposal_id": proposal_id,
            "before_head": before_head,
            "after_head": after_head,
            "verify": verify_results,
        }
        (proposal_dir / "apply_meta.json").write_text(
            json.dumps(apply_meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        mark_applied(proposal_id)
        log_event(
            event_type="proposal.applied",
            action="apply",
            status="success",
            actor_user_id=token.get("user_id"),
            actor_device_id=token.get("device_id"),
            resource_type="proposal",
            resource_id=proposal_id,
            message="proposal applied",
            meta={"before_head": before_head, "after_head": after_head, "verify": verify_results},
        )
        return {"applied": True}

    except HTTPException:
        if patch_path:
            _rollback_patch(patch_path, workdir)
        mark_rejected(proposal_id)
        log_event(
            event_type="proposal.rolled_back",
            action="rollback",
            status="success",
            actor_user_id=token.get("user_id"),
            actor_device_id=token.get("device_id"),
            resource_type="proposal",
            resource_id=proposal_id,
            message="proposal rolled back",
            meta={"before_head": before_head, "verify": verify_results},
        )
        raise
