# -*- coding: utf-8 -*-
"""Dependency vulnerability scanner (Layer 1).

Scans project dependencies for known vulnerabilities by checking
against package registries. Provides both CLI and programmatic API.

Usage:
    # Check from CLI
    python -m api.security.dependency_scanner

    # Programmatic
    from api.security.dependency_scanner import scan_requirements
    results = scan_requirements("requirements.txt")
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("automation_hub.security.dependency_scanner")

# Paths to scan relative to the project root
_DEFAULT_REQUIREMENT_PATHS = [
    "requirements.txt",
    "requirements-desktop.txt",
    "requirements-build.txt",
    "automation_hub/requirements.txt",
    "automation_hub/requirements-cli.txt",
]


@dataclass
class Dependency:
    """A single dependency entry."""
    name: str
    version: str | None = None
    source_file: str = ""


@dataclass
class Vulnerability:
    """A known vulnerability."""
    package: str
    installed_version: str
    vulnerability_id: str
    severity: str
    fixed_version: str | None = None
    description: str = ""


@dataclass
class ScanResult:
    """Result of a dependency scan."""
    dependencies: list[Dependency] = field(default_factory=list)
    vulnerabilities: list[Vulnerability] = field(default_factory=list)
    files_scanned: list[str] = field(default_factory=list)
    error: str | None = None


def parse_requirements_file(filepath: str) -> list[Dependency]:
    """Parse a pip requirements.txt file and extract dependencies.

    Args:
        filepath: Path to the requirements file.

    Returns:
        List of Dependency objects.
    """
    deps: list[Dependency] = []
    path = Path(filepath)
    if not path.exists():
        logger.debug("Requirements file not found: %s", filepath)
        return deps

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        # Skip comments, empty lines, and options
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Skip editable installs
        if line.startswith("-e"):
            continue
        # Handle ==, >=, <=, ~= version specifiers
        for separator in ("==", ">=", "<=", "~=", "!="):
            if separator in line:
                parts = line.split(separator, 1)
                name = parts[0].strip()
                version = parts[1].strip()
                deps.append(Dependency(name=name, version=version, source_file=filepath))
                break
        else:
            # No version specifier
            name = line.strip()
            if name:
                deps.append(Dependency(name=name, source_file=filepath))

    return deps


def _run_pip_audit(project_root: str) -> ScanResult:
    """Run pip-audit if available to scan for vulnerabilities.

    Args:
        project_root: The root directory of the project.

    Returns:
        ScanResult with findings.
    """
    result = ScanResult()
    try:
        output = subprocess.check_output(
            [sys.executable, "-m", "pip_audit", "--format", "json"],
            cwd=project_root,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
        audit_data = json.loads(output.decode())
        dependencies = audit_data.get("dependencies", [])
        for dep in dependencies:
            name = dep.get("name", "unknown")
            version = dep.get("version", "")
            vulns = dep.get("vulnerabilities", [])
            result.dependencies.append(Dependency(name=name, version=version))
            for vuln in vulns:
                result.vulnerabilities.append(Vulnerability(
                    package=name,
                    installed_version=version,
                    vulnerability_id=vuln.get("id", "unknown"),
                    severity=vuln.get("severity", "unknown"),
                    fixed_version=vuln.get("fixed_version"),
                    description=vuln.get("description", ""),
                ))
        return result
    except FileNotFoundError:
        result.error = "pip-audit not installed. Install with: pip install pip-audit"
        return result
    except subprocess.CalledProcessError as e:
        result.error = f"pip-audit failed: {e.output.decode() if e.output else str(e)}"
        return result
    except json.JSONDecodeError as e:
        result.error = f"Failed to parse pip-audit output: {e}"
        return result
    except subprocess.TimeoutExpired:
        result.error = "pip-audit timed out after 120 seconds"
        return result


def scan_requirements(requirements_path: str) -> ScanResult:
    """Scan a requirements file for dependencies.

    Args:
        requirements_path: Path to the requirements file.

    Returns:
        ScanResult with parsed dependencies (vulnerabilities require pip-audit).
    """
    result = ScanResult()
    deps = parse_requirements_file(requirements_path)
    result.dependencies = deps
    result.files_scanned = [requirements_path]
    return result


def scan_project(project_root: str = ".") -> ScanResult:
    """Scan all project requirement files for vulnerabilities.

    Args:
        project_root: The project root directory.

    Returns:
        ScanResult aggregating findings from all requirement files.
    """
    result = ScanResult()
    all_deps: dict[str, Dependency] = {}

    for req_path in _DEFAULT_REQUIREMENT_PATHS:
        full_path = str(Path(project_root) / req_path)
        deps = parse_requirements_file(full_path)
        for dep in deps:
            if dep.name not in all_deps:
                all_deps[dep.name] = dep
        if deps:
            result.files_scanned.append(full_path)

    result.dependencies = list(all_deps.values())

    # Try pip-audit for vulnerability scanning
    pip_audit_result = _run_pip_audit(project_root)
    result.vulnerabilities = pip_audit_result.vulnerabilities
    if pip_audit_result.error and not result.vulnerabilities:
        # Only report error if we have no results at all
        logger.warning("pip-audit error: %s", pip_audit_result.error)

    return result


def format_scan_report(result: ScanResult) -> str:
    """Format a scan result as a human-readable report.

    Args:
        result: The ScanResult to format.

    Returns:
        Formatted report string.
    """
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("DEPENDENCY VULNERABILITY SCAN REPORT")
    lines.append("=" * 60)
    lines.append(f"Files scanned: {len(result.files_scanned)}")
    lines.append(f"Dependencies found: {len(result.dependencies)}")
    lines.append(f"Vulnerabilities found: {len(result.vulnerabilities)}")
    lines.append("")

    if result.files_scanned:
        lines.append("Scanned files:")
        for f in result.files_scanned:
            lines.append(f"  - {f}")
        lines.append("")

    if result.dependencies:
        lines.append("Dependencies:")
        for dep in sorted(result.dependencies, key=lambda d: d.name):
            version_str = f"=={dep.version}" if dep.version else " (no version pin)"
            lines.append(f"  - {dep.name}{version_str}")
        lines.append("")

    if result.vulnerabilities:
        lines.append("VULNERABILITIES:")
        for vuln in sorted(result.vulnerabilities, key=lambda v: (v.severity, v.package)):
            lines.append(f"  [{vuln.severity.upper()}] {vuln.package} {vuln.installed_version}")
            lines.append(f"    ID: {vuln.vulnerability_id}")
            if vuln.fixed_version:
                lines.append(f"    Fix: upgrade to {vuln.fixed_version}")
            if vuln.description:
                lines.append(f"    {vuln.description[:200]}")
            lines.append("")

    if result.error:
        lines.append(f"Note: {result.error}")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> int:
    """Run the dependency scanner from CLI."""
    import argparse

    parser = argparse.ArgumentParser(description="Scan project dependencies for vulnerabilities")
    parser.add_argument("--project-root", default=".", help="Project root directory")
    parser.add_argument("--requirements", help="Single requirements file to scan")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.requirements:
        result = scan_requirements(args.requirements)
    else:
        result = scan_project(args.project_root)

    if args.json:
        print(json.dumps({
            "files_scanned": result.files_scanned,
            "dependencies": [{"name": d.name, "version": d.version} for d in result.dependencies],
            "vulnerabilities": [
                {
                    "package": v.package,
                    "installed_version": v.installed_version,
                    "vulnerability_id": v.vulnerability_id,
                    "severity": v.severity,
                    "fixed_version": v.fixed_version,
                }
                for v in result.vulnerabilities
            ],
            "error": result.error,
        }, indent=2))
    else:
        print(format_scan_report(result))

    return 1 if result.vulnerabilities else 0


if __name__ == "__main__":
    sys.exit(main())
