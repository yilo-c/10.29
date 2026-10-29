from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi import HTTPException

from automation_hub.api.auth.deps import require_scope
from automation_hub.api.db import (
    delete_personal_task,
    get_personal_task,
    list_personal_tasks,
    upsert_personal_task,
)
from automation_hub.api.routes.personal_factory import PersonalEntity, create_personal_router

router: APIRouter = create_personal_router(PersonalEntity(
    prefix="/personal/tasks",
    tag="personal-tasks",
    scope_read="task:read",
    scope_write="task:write",
    response_key="task",
    detail_not_found="task not found",
    db_list=list_personal_tasks,
    db_get=get_personal_task,
    db_upsert=upsert_personal_task,
    db_delete=delete_personal_task,
))


# ── PATCH /{remote_id} (task-specific: status → is_completed) ──

@router.patch("/{remote_id}")
def patch_personal_task(
    remote_id: str,
    payload: dict[str, Any],
    token=Depends(require_scope("task:write")),
):
    """Partial update for task — supports status toggle and field updates."""
    existing = get_personal_task(token["user_id"], remote_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="task not found")

    # Map convenience status field to is_completed
    if "status" in payload:
        status = payload.pop("status")
        if status == "completed":
            payload["is_completed"] = True
        elif status in ("todo", "in_progress"):
            payload["is_completed"] = False

    task = upsert_personal_task(token["user_id"], payload, remote_id=remote_id)
    return {"task": task}
