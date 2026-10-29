"""仓库索引与搜索路由（最小版）。"""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from automation_hub.api.auth.deps import require_scope
from automation_hub.api.db import get_db_connection
from automation_hub.api.repos.indexer import build_index
from automation_hub.api.repos.models import RepoCreate, RepoResponse, RepoSearchMatch, RepoSearchResponse

router = APIRouter(prefix="/repos", tags=["repos"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_match(relative: str, line_number: int, line: str) -> str:
    return f"{relative}:{line_number}:{line.rstrip()}"


def _parse_match(raw: str) -> RepoSearchMatch:
    path, line_number, text = raw.split(":", 2)
    # rg 以搜索根 "." 为基准输出路径：Windows 下带 ".\\ " 前缀且用反斜杠，
    # python 引擎（_python_search）则输出 as_posix() 相对路径——两种引擎
    # 输出必须同构，这里统一归一化为 posix 相对路径。
    norm = path.replace("\\", "/")
    if norm.startswith("./"):
        norm = norm[2:]
    return RepoSearchMatch(path=norm, line=int(line_number), text=text)


def _python_search(root: Path, query: str, limit: int, offset: int) -> tuple[int, list[str]]:
    matches: list[str] = []
    total = 0
    skipped = int(offset)

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        try:
            relative = path.relative_to(root).as_posix()
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if query not in line:
                        continue
                    total += 1
                    if skipped > 0:
                        skipped -= 1
                        continue
                    if len(matches) < int(limit):
                        matches.append(_format_match(relative, line_number, line))
        except OSError:
            continue

    return total, matches


def _build_search_response(query: str, engine: str, total: int, limit: int, offset: int, lines: list[str]) -> RepoSearchResponse:
    return RepoSearchResponse(
        query=query,
        engine=engine,
        total=int(total),
        limit=int(limit),
        offset=int(offset),
        matches=lines,
        items=[_parse_match(line) for line in lines],
    )


def _search_repo_or_raise(repo_id: str, query: str, limit: int, offset: int) -> tuple[dict, RepoSearchResponse]:
    repo = _get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="repo not found")

    root = Path(repo["root_path"]).resolve()
    if not root.exists():
        raise HTTPException(status_code=400, detail="root_path not found")

    cmd = [
        "rg",
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        query,
        ".",
    ]
    try:
        res = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
    except FileNotFoundError:
        total, lines = _python_search(root, query, int(limit), int(offset))
        return repo, _build_search_response(query, "python", total, int(limit), int(offset), lines)

    if res.returncode not in (0, 1):
        raise HTTPException(status_code=500, detail=res.stderr.strip() or "rg failed")

    lines = [line for line in (res.stdout or "").splitlines() if line.strip()]
    start = int(offset)
    end = start + int(limit)
    return repo, _build_search_response(query, "rg", len(lines), int(limit), int(offset), lines[start:end])


def _get_repo(repo_id: str) -> dict | None:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
        return dict(row) if row else None


@router.post("", response_model=RepoResponse)
def create_repo(payload: RepoCreate, token=Depends(require_scope("repo:write"))):
    root = Path(payload.root_path).resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail="root_path not found or not a directory")

    repo_id = str(uuid.uuid4())
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO repos(id,name,root_path,created_at) VALUES(?,?,?,?)",
            (repo_id, payload.name, str(root), now_iso()),
        )
        conn.commit()

    return RepoResponse(
        id=repo_id,
        name=payload.name,
        root_path=str(root),
        created_at=now_iso(),
        last_indexed_at=None,
    )


@router.get("", response_model=List[RepoResponse])
def list_repos(limit: int = Query(50, ge=1, le=200), token=Depends(require_scope("repo:read"))):
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM repos ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [RepoResponse(**dict(r)) for r in rows]


@router.post("/{repo_id}/index")
def index_repo(repo_id: str, token=Depends(require_scope("repo:write"))):
    repo = _get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="repo not found")

    root = Path(repo["root_path"]).resolve()
    if not root.exists():
        raise HTTPException(status_code=400, detail="root_path not found")

    with get_db_connection() as conn:
        existing_rows = conn.execute(
            "SELECT path, mtime, size, sha256 FROM repo_files WHERE repo_id=?",
            (repo_id,),
        ).fetchall()
        existing = {r["path"]: dict(r) for r in existing_rows}

    entries = build_index(root, existing)
    files = [
        (
            str(uuid.uuid4()),
            repo_id,
            e["path"],
            e["mtime"],
            e["size"],
            e["sha256"],
        )
        for e in entries
    ]

    with get_db_connection() as conn:
        conn.execute("DELETE FROM repo_files WHERE repo_id=?", (repo_id,))
        conn.executemany(
            "INSERT INTO repo_files(id,repo_id,path,mtime,size,sha256) VALUES(?,?,?,?,?,?)",
            files,
        )
        conn.execute(
            "UPDATE repos SET last_indexed_at=? WHERE id=?",
            (now_iso(), repo_id),
        )
        conn.commit()

    return {"indexed": True, "files": len(files)}


@router.get("/{repo_id}/files")
def list_repo_files(
    repo_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    token=Depends(require_scope("repo:read")),
):
    repo = _get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="repo not found")

    with get_db_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM repo_files WHERE repo_id=?",
            (repo_id,),
        ).fetchone()["c"]
        rows = conn.execute(
            "SELECT path, mtime, size, sha256 FROM repo_files WHERE repo_id=? ORDER BY path LIMIT ? OFFSET ?",
            (repo_id, int(limit), int(offset)),
        ).fetchall()
        return {"total": total, "files": [dict(r) for r in rows]}


@router.get("/{repo_id}/search", response_model=RepoSearchResponse)
def search_repo(
    repo_id: str,
    q: str = Query(..., min_length=1),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    token=Depends(require_scope("repo:read")),
):
    _, search_payload = _search_repo_or_raise(repo_id, q, int(limit), int(offset))
    return search_payload
