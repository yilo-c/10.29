from dataclasses import dataclass, field
from typing import Any


@dataclass
class RadarFinding:
    name: str = ""
    current_version: str = ""
    latest_version: str = ""
    url: str = ""
    description: str = ""
    level: int = 1
    has_breaking_changes: bool = False
    source: str = ""
    category: str = ""
    found_at: str = ""


class TechRadar:
    def get_all_findings(self, since: str | None = None) -> list[RadarFinding]:
        return []

    def scan(self, target: str | None = None) -> list[RadarFinding]:
        return []

    def get_status_snapshot(self) -> dict[str, Any]:
        return {"status": "stub", "findings_count": 0}
