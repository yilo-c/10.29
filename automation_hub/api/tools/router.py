from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from automation_hub.api.auth.deps import require_scope
from automation_hub.api.tools.models import ToolUpsert
from automation_hub.api.tools.registry import upsert_tool, get_tool, list_tools, set_tool_enabled

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("")
def _list(token=Depends(require_scope("tool:read"))):
    tools = list_tools()
    return {"tools": tools, "count": len(tools)}


@router.get("/{tool_id}")
def _get(tool_id: str, token=Depends(require_scope("tool:read"))):
    t = get_tool(tool_id)
    if not t:
        raise HTTPException(status_code=404, detail="not found")
    return t


@router.post("")
def _upsert(payload: ToolUpsert, token=Depends(require_scope("tool:write"))):
    upsert_tool(payload.model_dump())
    return {"ok": True, "tool_id": payload.id}


@router.post("/{tool_id}/enable")
def _enable(tool_id: str, token=Depends(require_scope("tool:write"))):
    if not set_tool_enabled(tool_id, True):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "tool_id": tool_id, "is_enabled": True}


@router.post("/{tool_id}/disable")
def _disable(tool_id: str, token=Depends(require_scope("tool:write"))):
    if not set_tool_enabled(tool_id, False):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "tool_id": tool_id, "is_enabled": False}
