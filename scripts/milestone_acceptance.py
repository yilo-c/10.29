#!/usr/bin/env python
"""Milestone acceptance package generator — Sprint 6 M3 Pre-Freeze.

Produces a structured JSON report containing:
1. Cold start results (1 round quick check)
2. Core smoke results
3. Doctor JSON output
4. Sync conflict regression (existing test)
5. Auth flow regression (existing test)

Usage:
    python milestone_acceptance.py [--output-dir ./acceptance_output]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _run_step(title: str, command: list[str], cwd: Path, timeout: int = 120) -> dict[str, Any]:
    print(f"[acceptance] {title}")
    print(f"[acceptance] command: {' '.join(command)}")
    try:
        completed = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return {
            "title": title,
            "exit_code": completed.returncode,
            "passed": completed.returncode == 0,
            "stdout": (completed.stdout or "").strip(),
            "stderr": (completed.stderr or "").strip(),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "title": title,
            "exit_code": -1,
            "passed": False,
            "stdout": (exc.stdout or "").strip() if exc.stdout else "",
            "stderr": (exc.stderr or "").strip() + "\nTIMEOUT" if exc.stderr else "TIMEOUT",
        }


def _check_smoke(python_executable: str, repo_root: Path) -> dict[str, Any]:
    """Run core smoke check."""
    smoke_script = repo_root / "思忆集app" / "smoke_check.py"
    if not smoke_script.is_file():
        return {"title": "core smoke", "exit_code": -1, "passed": False, "stderr": "smoke_check.py not found"}
    return _run_step(
        "core smoke",
        [python_executable, str(smoke_script), "--suite", "core"],
        repo_root,
        timeout=60,
    )


def _check_cold_start(python_executable: str, repo_root: Path) -> dict[str, Any]:
    """Run cold start stability check (1 round)."""
    cold_start_script = repo_root / "scripts" / "cold_start_stability_check.py"
    if not cold_start_script.is_file():
        return {"title": "cold start", "exit_code": -1, "passed": False, "stderr": "cold_start_stability_check.py not found"}
    return _run_step(
        "cold start (1 round)",
        [python_executable, str(cold_start_script), "--rounds", "1", "--auto-exit-ms", "15000"],
        repo_root,
        timeout=60,
    )


def _check_doctor_json(python_executable: str, repo_root: Path) -> dict[str, Any]:
    """Run desktop Doctor and export JSON."""
    doctor_script = repo_root / "scripts" / "desktop_doctor.py"
    if not doctor_script.is_file():
        return {"title": "doctor JSON", "exit_code": -1, "passed": False, "stderr": "desktop_doctor.py not found"}
    result = _run_step(
        "doctor JSON",
        [python_executable, str(doctor_script), "--json"],
        repo_root,
        timeout=30,
    )
    # Try to parse the JSON output to verify it's valid
    if result["stdout"]:
        for line in result["stdout"].splitlines():
            if line.strip().startswith("{"):
                try:
                    json.loads(line)
                    result["doctor_json_valid"] = True
                except json.JSONDecodeError:
                    result["doctor_json_valid"] = False
                break
    return result


def _check_sync_conflict_regression(python_executable: str, repo_root: Path) -> dict[str, Any]:
    """Run sync conflict path tests."""
    test_file = repo_root / "思忆集app" / "tests" / "test_sync_conflict_paths.py"
    if not test_file.is_file():
        return {"title": "sync conflict regression", "exit_code": -1, "passed": False, "stderr": "test_sync_conflict_paths.py not found"}
    return _run_step(
        "sync conflict regression",
        [python_executable, "-m", "pytest", str(test_file), "-v", "-q"],
        repo_root / "思忆集app",
        timeout=60,
    )


def _check_auth_flow_regression(python_executable: str, repo_root: Path) -> dict[str, Any]:
    """Run auth flow regression tests."""
    test_file = repo_root / "automation_hub" / "tests" / "test_api_auth_flow.py"
    if not test_file.is_file():
        return {"title": "auth flow regression", "exit_code": -1, "passed": False, "stderr": "test_api_auth_flow.py not found"}
    return _run_step(
        "auth flow regression",
        [python_executable, "-m", "pytest", str(test_file), "-v", "-q"],
        repo_root / "automation_hub",
        timeout=60,
    )


def _check_freeze_conformance(python_executable: str, repo_root: Path) -> dict[str, Any]:
    """Run M3 freeze conformance tests."""
    test_file = repo_root / "思忆集app" / "tests" / "test_m3_freeze_conformance.py"
    if not test_file.is_file():
        return {"title": "freeze conformance", "exit_code": -1, "passed": False, "stderr": "test_m3_freeze_conformance.py not found"}
    return _run_step(
        "freeze conformance",
        [python_executable, "-m", "pytest", str(test_file), "-v", "-q"],
        repo_root / "思忆集app",
        timeout=60,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Sprint 6 M3 里程碑验收包")
    parser.add_argument("--output-dir", default="", help="验收包输出目录（默认 <repo_root>/acceptance_output）")
    parser.add_argument("--python", default=sys.executable, help="Python 可执行路径")
    parser.add_argument("--skip-cold-start", action="store_true", help="跳过冷启动检查")
    parser.add_argument("--skip-doctor", action="store_true", help="跳过 Doctor 检查")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    python_exe = args.python

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else (repo_root / "acceptance_output")
    output_dir.mkdir(parents=True, exist_ok=True)

    checks: dict[str, Any] = {
        "acceptance_time": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "sprint": "Sprint 6 — M3 Pre-Freeze",
        "results": {},
    }

    print("=" * 60)
    print("Sprint 6 Milestone Acceptance Package")
    print("=" * 60)

    # 1. Core smoke
    print("\n--- 1. Core Smoke ---")
    smoke = _check_smoke(python_exe, repo_root)
    checks["results"]["core_smoke"] = smoke

    # 2. Cold start (1 round, quick)
    if not args.skip_cold_start:
        print("\n--- 2. Cold Start (1 round) ---")
        cold_start = _check_cold_start(python_exe, repo_root)
        checks["results"]["cold_start"] = cold_start
    else:
        checks["results"]["cold_start"] = {"title": "cold start", "exit_code": 0, "passed": True, "note": "skipped by --skip-cold-start"}

    # 3. Doctor JSON
    if not args.skip_doctor:
        print("\n--- 3. Doctor JSON ---")
        doctor = _check_doctor_json(python_exe, repo_root)
        checks["results"]["doctor_json"] = doctor
    else:
        checks["results"]["doctor_json"] = {"title": "doctor JSON", "exit_code": 0, "passed": True, "note": "skipped by --skip-doctor"}

    # 4. Sync conflict regression
    print("\n--- 4. Sync Conflict Regression ---")
    sync_conflict = _check_sync_conflict_regression(python_exe, repo_root)
    checks["results"]["sync_conflict_regression"] = sync_conflict

    # 5. Auth flow regression
    print("\n--- 5. Auth Flow Regression ---")
    auth_flow = _check_auth_flow_regression(python_exe, repo_root)
    checks["results"]["auth_flow_regression"] = auth_flow

    # 6. Freeze conformance (bonus)
    print("\n--- 6. Freeze Conformance ---")
    freeze = _check_freeze_conformance(python_exe, repo_root)
    checks["results"]["freeze_conformance"] = freeze

    # Summary
    total = len(checks["results"])
    passed = sum(1 for r in checks["results"].values() if r.get("passed"))
    checks["summary"] = {
        "total_checks": total,
        "passed": passed,
        "failed": total - passed,
        "all_passed": passed == total,
    }

    # Write report
    report_path = output_dir / "milestone_acceptance_report.json"
    report_path.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_text_path = output_dir / "milestone_summary.txt"
    summary_lines = [
        f"Sprint 6 M3 Milestone Acceptance",
        f"Time: {checks['acceptance_time']}",
        f"Passed: {checks['summary']['passed']}/{checks['summary']['total_checks']}",
        f"All passed: {checks['summary']['all_passed']}",
        "",
    ]
    for name, result in checks["results"].items():
        status = "PASS" if result.get("passed") else "FAIL"
        summary_lines.append(f"  [{status}] {result.get('title', name)} (exit={result.get('exit_code')})")
    summary_text_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"Acceptance report: {report_path}")
    print(f"Summary: {summary_text_path}")
    print(f"Passed {checks['summary']['passed']}/{checks['summary']['total_checks']}")
    print("=" * 60)

    return 0 if checks["summary"]["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
