"""Workflow engine management API routes.

Provides CRUD, execution, and lifecycle management for workflows.
In-memory storage — TODO: replace with database.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Any
import logging
import uuid
import datetime

from automation_hub.api.auth.deps import require_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflows"])


# ── In-memory storage ──────────────────────────────────────────────

_WORKFLOWS: dict[str, dict[str, Any]] = {}
_EXECUTIONS: dict[str, dict[str, Any]] = {}


# ── Models ─────────────────────────────────────────────────────────


class WorkflowStep(BaseModel):
    """A single step in a workflow."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique step identifier")
    name: str = Field(description="Step name", min_length=1, max_length=200)
    type: str = Field(description="Step type: tool|action|condition|wait")
    config: dict[str, Any] = Field(default_factory=dict, description="Step-specific configuration")
    depends_on: list[str] = Field(default_factory=list, description="IDs of steps that must complete first")


class CreateWorkflowRequest(BaseModel):
    """Request body for creating a new workflow."""

    name: str = Field(description="Workflow name", min_length=1, max_length=200)
    steps: list[WorkflowStep] = Field(description="Ordered list of workflow steps", min_length=1)
    trigger: str | None = Field(default=None, description="Optional trigger type: schedule|webhook|event")


class WorkflowItem(BaseModel):
    """Basic workflow info for list responses."""

    id: str
    name: str
    enabled: bool
    step_count: int
    trigger: str | None = None
    created_at: str
    updated_at: str


class WorkflowDetail(WorkflowItem):
    """Full workflow detail including steps."""

    steps: list[dict[str, Any]] = Field(default_factory=list)
    last_execution: dict[str, Any] | None = None


class ExecutionResult(BaseModel):
    """Result of a workflow execution."""

    execution_id: str
    workflow_id: str
    status: str
    started_at: str
    completed_at: str | None = None
    step_results: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class ToggleRequest(BaseModel):
    enabled: bool


# ── Helpers ────────────────────────────────────────────────────────


def _get_workflow_or_404(workflow_id: str) -> dict[str, Any]:
    workflow = _WORKFLOWS.get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"workflow not found: {workflow_id}")
    return workflow


def _now() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _build_workflow_item(workflow_id: str, workflow: dict[str, Any]) -> WorkflowItem:
    return WorkflowItem(
        id=workflow_id,
        name=workflow["name"],
        enabled=workflow["enabled"],
        step_count=len(workflow["steps"]),
        trigger=workflow.get("trigger"),
        created_at=workflow["created_at"],
        updated_at=workflow["updated_at"],
    )


def _build_workflow_detail(workflow_id: str) -> WorkflowDetail:
    workflow = _get_workflow_or_404(workflow_id)
    detail = WorkflowDetail(
        id=workflow_id,
        name=workflow["name"],
        enabled=workflow["enabled"],
        step_count=len(workflow["steps"]),
        trigger=workflow.get("trigger"),
        created_at=workflow["created_at"],
        updated_at=workflow["updated_at"],
        steps=[dict(s) if isinstance(s, BaseModel) else s for s in workflow["steps"]],
    )
    # Attach latest execution if available
    executions = [
        e for e in _EXECUTIONS.values()
        if e["workflow_id"] == workflow_id
    ]
    if executions:
        latest = max(executions, key=lambda e: e["started_at"])
        detail.last_execution = {
            "execution_id": latest["execution_id"],
            "status": latest["status"],
            "started_at": latest["started_at"],
            "completed_at": latest.get("completed_at"),
        }
    return detail


# ── Endpoints ──────────────────────────────────────────────────────


@router.get("", response_model=list[WorkflowItem])
async def list_workflows(
    include_disabled: bool = False,
    token=Depends(require_scope("workflows:admin")),
):
    """List all workflows.

    By default only returns enabled workflows. Set ``include_disabled=true``
    to include disabled workflows.
    """
    try:
        result: list[WorkflowItem] = []
        for wid, workflow in _WORKFLOWS.items():
            if not include_disabled and not workflow.get("enabled", False):
                continue
            result.append(_build_workflow_item(wid, workflow))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list workflows failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=WorkflowDetail, status_code=201)
async def create_workflow(
    body: CreateWorkflowRequest,
    token=Depends(require_scope("workflows:admin")),
):
    """Create a new workflow with the given steps."""
    try:
        workflow_id = str(uuid.uuid4())
        now = _now()
        workflow: dict[str, Any] = {
            "name": body.name,
            "steps": [step.model_dump() for step in body.steps],
            "trigger": body.trigger,
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        }
        _WORKFLOWS[workflow_id] = workflow
        logger.info("workflow created: %s (%s)", workflow_id, body.name)
        return _build_workflow_detail(workflow_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("create workflow failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(
    workflow_id: str,
    token=Depends(require_scope("workflows:admin")),
):
    """Get full details for a specific workflow."""
    try:
        return _build_workflow_detail(workflow_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get workflow failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: str,
    token=Depends(require_scope("workflows:admin")),
):
    """Delete a workflow by ID."""
    try:
        _get_workflow_or_404(workflow_id)
        _WORKFLOWS.pop(workflow_id, None)
        # Clean up associated executions
        stale = [
            eid for eid, e in _EXECUTIONS.items()
            if e["workflow_id"] == workflow_id
        ]
        for eid in stale:
            _EXECUTIONS.pop(eid, None)
        logger.info("workflow deleted: %s", workflow_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("delete workflow failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{workflow_id}/execute", response_model=ExecutionResult)
async def execute_workflow(
    workflow_id: str,
    token=Depends(require_scope("workflows:admin")),
):
    """Execute a workflow synchronously.

    Runs each step in sequence (respecting dependencies) and returns
    the execution results.
    """
    try:
        workflow = _get_workflow_or_404(workflow_id)
        if not workflow.get("enabled", False):
            raise HTTPException(status_code=400, detail="workflow is disabled")

        execution_id = str(uuid.uuid4())
        now = _now()

        execution: dict[str, Any] = {
            "execution_id": execution_id,
            "workflow_id": workflow_id,
            "status": "running",
            "started_at": now,
            "completed_at": None,
            "step_results": [],
            "error": None,
        }
        _EXECUTIONS[execution_id] = execution

        steps = workflow.get("steps", [])
        step_results: list[dict[str, Any]] = []
        completed: set[str] = set()
        failed = False

        # Simple topological execution: run steps whose dependencies are met
        remaining = list(steps)
        while remaining and not failed:
            batch = [s for s in remaining if all(d in completed for d in s.get("depends_on", []))]
            if not batch:
                # Circular dependency or incomplete deps
                failed = True
                execution["status"] = "failed"
                execution["error"] = "circular or unmet dependencies detected"
                break

            for step in batch:
                remaining.remove(step)
                try:
                    # TODO: replace with actual step execution logic
                    step_result = {
                        "step_id": step["id"],
                        "step_name": step["name"],
                        "status": "completed",
                        "output": {"message": f"step '{step['name']}' executed successfully"},
                    }
                    step_results.append(step_result)
                    completed.add(step["id"])
                except Exception as step_err:
                    step_results.append({
                        "step_id": step["id"],
                        "step_name": step["name"],
                        "status": "failed",
                        "error": str(step_err),
                    })
                    execution["status"] = "failed"
                    execution["error"] = f"step '{step['name']}' failed: {step_err}"
                    failed = True
                    break

        if not failed:
            execution["status"] = "completed"

        execution["completed_at"] = _now()
        execution["step_results"] = step_results

        return ExecutionResult(
            execution_id=execution_id,
            workflow_id=workflow_id,
            status=execution["status"],
            started_at=execution["started_at"],
            completed_at=execution["completed_at"],
            step_results=step_results,
            error=execution.get("error"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("execute workflow failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{workflow_id}/toggle", response_model=WorkflowDetail)
async def toggle_workflow(
    workflow_id: str,
    body: ToggleRequest,
    token=Depends(require_scope("workflows:admin")),
):
    """Enable or disable a workflow."""
    try:
        workflow = _get_workflow_or_404(workflow_id)
        workflow["enabled"] = body.enabled
        workflow["updated_at"] = _now()
        logger.info("workflow %s toggled to enabled=%s", workflow_id, body.enabled)
        return _build_workflow_detail(workflow_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("toggle workflow failed")
        raise HTTPException(status_code=500, detail=str(e))
