"""Proposals management API routes.

Provides CRUD for improvement proposals.
In-memory storage — TODO: replace with database.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Any
import logging
import uuid
from datetime import datetime, timezone

from automation_hub.api.auth.deps import require_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proposals", tags=["proposals"])


# ── In-memory storage ──────────────────────────────────────────────

_PROPOSALS: dict[str, dict[str, Any]] = {}


# ── Models ─────────────────────────────────────────────────────────

class ProposalCreate(BaseModel):
    """Request to create a new proposal."""

    title: str = Field(description="Proposal title", min_length=1, max_length=200)
    content: str = Field(description="Proposal content", min_length=1)
    tags: list[str] = Field(default_factory=list, description="Optional tags")


class ProposalUpdate(BaseModel):
    """Request to update an existing proposal. All fields optional."""

    title: str | None = Field(default=None, description="Proposal title", max_length=200)
    content: str | None = Field(default=None, description="Proposal content")
    status: str | None = Field(default=None, description="Proposal status")
    tags: list[str] | None = Field(default=None, description="Optional tags")


class ProposalItem(BaseModel):
    """Proposal response model."""

    id: str
    title: str
    content: str
    status: str
    tags: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


# ── Endpoints ──────────────────────────────────────────────────────


@router.get("", response_model=list[ProposalItem])
async def list_proposals(
    status: str | None = None,
    token=Depends(require_scope("proposals:admin")),
):
    """List all proposals, optionally filtered by status.

    Results are sorted by updated_at descending.
    """
    try:
        items = list(_PROPOSALS.values())
        if status:
            items = [p for p in items if p.get("status") == status]
        items.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
        return [ProposalItem(**p) for p in items]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list proposals failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=ProposalItem, status_code=status.HTTP_201_CREATED)
async def create_proposal(
    body: ProposalCreate,
    token=Depends(require_scope("proposals:admin")),
):
    """Create a new proposal."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        proposal_id = str(uuid.uuid4())

        proposal = {
            "id": proposal_id,
            "title": body.title,
            "content": body.content,
            "status": "pending_review",
            "tags": body.tags or [],
            "created_at": now,
            "updated_at": now,
        }
        _PROPOSALS[proposal_id] = proposal
        logger.info("proposal created: %s", proposal_id)
        return ProposalItem(**proposal)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("create proposal failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{proposal_id}", response_model=ProposalItem)
async def get_proposal(
    proposal_id: str,
    token=Depends(require_scope("proposals:admin")),
):
    """Get a proposal by ID."""
    try:
        proposal = _PROPOSALS.get(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail=f"proposal not found: {proposal_id}")
        return ProposalItem(**proposal)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get proposal failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{proposal_id}", response_model=ProposalItem)
async def update_proposal(
    proposal_id: str,
    body: ProposalUpdate,
    token=Depends(require_scope("proposals:admin")),
):
    """Update an existing proposal. Only provided fields are updated."""
    try:
        proposal = _PROPOSALS.get(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail=f"proposal not found: {proposal_id}")

        updates = body.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="no fields to update")

        proposal.update(updates)
        proposal["updated_at"] = datetime.now(timezone.utc).isoformat()
        _PROPOSALS[proposal_id] = proposal
        logger.info("proposal updated: %s", proposal_id)
        return ProposalItem(**proposal)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update proposal failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{proposal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_proposal(
    proposal_id: str,
    token=Depends(require_scope("proposals:admin")),
):
    """Delete a proposal by ID."""
    try:
        if proposal_id not in _PROPOSALS:
            raise HTTPException(status_code=404, detail=f"proposal not found: {proposal_id}")
        del _PROPOSALS[proposal_id]
        logger.info("proposal deleted: %s", proposal_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("delete proposal failed")
        raise HTTPException(status_code=500, detail=str(e))
