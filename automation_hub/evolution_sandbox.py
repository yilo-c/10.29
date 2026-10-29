from dataclasses import dataclass, field
from typing import Any
from evolution_tech_radar import RadarFinding


@dataclass
class SandboxTestResult:
    test_name: str = ""
    status: str = "pending"
    output: str = ""
    error: str = ""
    duration_seconds: float = 0.0


@dataclass
class SandboxRun:
    run_id: str = ""
    sandbox_type: str = ""
    target_name: str = ""
    target_version: str = ""
    tests: list[SandboxTestResult] = field(default_factory=list)
    overall_status: str = "pending"
    started_at: str = ""
    completed_at: str = ""
    summary: str = ""


class EvolutionSandbox:
    def run_tests(self, finding: RadarFinding) -> SandboxRun:
        return SandboxRun(run_id="stub", overall_status="skipped")

    def get_run(self, run_id: str) -> SandboxRun | None:
        return None

    def get_recent_runs(self, limit: int = 10) -> list[SandboxRun]:
        return []

    def get_status_snapshot(self) -> dict[str, Any]:
        return {"status": "stub", "runs_count": 0}
