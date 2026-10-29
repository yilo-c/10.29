"""Evolution system management API routes.

Provides TechRadar scanning and Sandbox testing for controlled
technology evolution. In-memory storage — TODO: replace with database.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Any
import logging
import uuid
from datetime import datetime, timezone

from automation_hub.api.auth.deps import require_scope

from evolution_tech_radar import TechRadar, RadarFinding
from evolution_sandbox import EvolutionSandbox, SandboxRun, SandboxTestResult
from evolution_orchestrator import EvolutionOrchestrator, EvolutionResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evolution", tags=["evolution"])


# ── In-memory storage ──────────────────────────────────────────────

_TECH_RADAR: TechRadar = TechRadar()
_SANDBOX: EvolutionSandbox = EvolutionSandbox()
_ORCHESTRATOR: EvolutionOrchestrator = EvolutionOrchestrator(
    tech_radar=_TECH_RADAR,
    sandbox=_SANDBOX,
)


# ── Models ─────────────────────────────────────────────────────────

class RadarFindingItem(BaseModel):
    """Tech radar finding for list responses."""

    name: str
    current_version: str = ""
    latest_version: str = ""
    url: str = ""
    description: str = ""
    level: int = 1
    has_breaking_changes: bool = False
    source: str = ""
    category: str = ""
    found_at: str = ""


class RadarTargetItem(BaseModel):
    """A scan target in the tech radar."""

    name: str
    source: str
    category: str
    url: str = ""


class RadarTargetCreate(BaseModel):
    """Request to add a new scan target."""

    name: str = Field(description="Target name", min_length=1, max_length=200)
    source: str = Field(description="Source type: pypi | github_release | model_provider | changelog")
    category: str = Field(default="dependency", description="Category: dependency | model | tool | security")
    url: str = Field(default="", description="URL for release info")


class SandboxTestResultItem(BaseModel):
    """Single sandbox test result."""

    test_name: str
    status: str
    output: str = ""
    error: str = ""
    duration_seconds: float = 0.0


class SandboxRunItem(BaseModel):
    """Sandbox run summary for list responses."""

    run_id: str
    sandbox_type: str
    target_name: str
    target_version: str
    tests: list[SandboxTestResultItem] = Field(default_factory=list)
    overall_status: str
    started_at: str = ""
    completed_at: str = ""
    summary: str = ""


class SandboxRunRequest(BaseModel):
    """Request to run a sandbox test."""

    target_name: str = Field(description="Name of the target to test")
    target_version: str = Field(description="Version to test")


class EvolutionStatus(BaseModel):
    """Overall evolution system status."""

    orchestrator: dict[str, Any]
    radar: dict[str, Any]
    sandbox: dict[str, Any]


# ── Endpoints ──────────────────────────────────────────────────────


@router.get("/status", response_model=EvolutionStatus)
async def get_evolution_status(
    token=Depends(require_scope("evolution:admin")),
):
    """Get overall evolution system status.

    Returns combined status from the orchestrator, tech radar,
    and sandbox subsystems.
    """
    try:
        return EvolutionStatus(
            orchestrator=_ORCHESTRATOR.get_status_snapshot(),
            radar=_TECH_RADAR.get_status_snapshot(),
            sandbox=_SANDBOX.get_status_snapshot(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get evolution status failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/radar", response_model=list[RadarFindingItem])
async def list_radar_findings(
    since: str | None = None,
    token=Depends(require_scope("evolution:admin")),
):
    """List all tech radar findings (discovered updates).

    Optionally filter by ``since`` (ISO timestamp).
    """
    try:
        findings = _TECH_RADAR.get_all_findings(since=since)
        return [
            RadarFindingItem(
                name=f.name,
                current_version=f.current_version,
                latest_version=f.latest_version,
                url=f.url,
                description=f.description,
                level=f.level,
                has_breaking_changes=f.has_breaking_changes,
                source=f.source,
                category=f.category,
                found_at=f.found_at,
            )
            for f in findings
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list radar findings failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/radar", response_model=RadarTargetItem)
async def add_radar_target(
    body: RadarTargetCreate,
    token=Depends(require_scope("evolution:admin")),
):
    """Add a new scan target to the tech radar."""
    try:
        target = {
            "name": body.name,
            "source": body.source,
            "category": body.category,
            "url": body.url,
        }
        _TECH_RADAR._scan_targets.append(target)
        logger.info("radar target added: %s (%s)", body.name, body.source)
        return RadarTargetItem(
            name=body.name,
            source=body.source,
            category=body.category,
            url=body.url,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("add radar target failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/radar/scan", response_model=list[RadarFindingItem])
async def trigger_radar_scan(
    target: str | None = None,
    token=Depends(require_scope("evolution:admin")),
):
    """Trigger an immediate tech radar scan.

    Optionally scan a single target by name.
    """
    try:
        findings = _TECH_RADAR.scan(target=target)
        return [
            RadarFindingItem(
                name=f.name,
                current_version=f.current_version,
                latest_version=f.latest_version,
                url=f.url,
                description=f.description,
                level=f.level,
                has_breaking_changes=f.has_breaking_changes,
                source=f.source,
                category=f.category,
                found_at=f.found_at,
            )
            for f in findings
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("radar scan failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sandbox", response_model=list[SandboxRunItem])
async def list_sandbox_runs(
    limit: int = 10,
    token=Depends(require_scope("evolution:admin")),
):
    """List recent sandbox test runs."""
    try:
        runs = _SANDBOX.get_recent_runs(limit=limit)
        return [_sandbox_run_to_item(r) for r in runs]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list sandbox runs failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sandbox/run", response_model=SandboxRunItem)
async def run_sandbox_test(
    body: SandboxRunRequest,
    token=Depends(require_scope("evolution:admin")),
):
    """Run a sandbox test for a specific target and version.

    Creates a temporary RadarFinding and runs the full sandbox
    test suite against it.
    """
    try:
        finding = RadarFinding(
            name=body.target_name,
            latest_version=body.target_version,
            found_at=datetime.now(timezone.utc).isoformat(),
        )
        run = _SANDBOX.run_tests(finding)
        logger.info(
            "sandbox run %s for %s %s: %s",
            run.run_id, body.target_name, body.target_version, run.overall_status,
        )
        return _sandbox_run_to_item(run)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("sandbox run failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sandbox/{run_id}", response_model=SandboxRunItem)
async def get_sandbox_run(
    run_id: str,
    token=Depends(require_scope("evolution:admin")),
):
    """Get details for a specific sandbox run."""
    try:
        run = _SANDBOX.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"sandbox run not found: {run_id}")
        return _sandbox_run_to_item(run)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get sandbox run failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── Helpers ────────────────────────────────────────────────────────


def _sandbox_run_to_item(run: SandboxRun) -> SandboxRunItem:
    """Convert a SandboxRun dataclass to a SandboxRunItem model."""
    return SandboxRunItem(
        run_id=run.run_id,
        sandbox_type=run.sandbox_type,
        target_name=run.target_name,
        target_version=run.target_version,
        tests=[
            SandboxTestResultItem(
                test_name=t.test_name,
                status=t.status,
                output=t.output,
                error=t.error,
                duration_seconds=t.duration_seconds,
            )
            for t in run.tests
        ],
        overall_status=run.overall_status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        summary=run.summary,
    )
