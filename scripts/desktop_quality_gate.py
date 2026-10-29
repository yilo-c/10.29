from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _resolve_default_channel(suite: str) -> str:
    if suite in {"release", "all"}:
        return "stable"
    return "dev"


def _run_step(title: str, command: list[str], cwd: Path) -> None:
    print(f"[desktop-gate] {title}")
    print(f"[desktop-gate] command: {' '.join(command)}")
    subprocess.run(command, cwd=str(cwd), check=True)


def _build_release_version(channel: str, build: int) -> str:
    now = datetime.now()
    if channel == "stable":
        year, week, _ = now.isocalendar()
        return f"{year}.{week}.0"
    return f"{now.year}.{now.month:02d}{now.day:02d}-dev.{max(1, int(build))}"


def main() -> int:
    parser = argparse.ArgumentParser(description="思忆集桌面端统一质量门禁：smoke + 发布报告")
    parser.add_argument("--suite", choices=["core", "ui", "p2", "all", "release"], default="p2")
    parser.add_argument("--channel", choices=["dev", "stable", "auto"], default="auto")
    parser.add_argument("--build", type=int, default=1)
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--feature-changes-file", default="")
    parser.add_argument("--known-issues-file", default="")
    parser.add_argument("--rollback-steps-file", default="")
    parser.add_argument("--rollback-version", default="上一个稳定版本")
    parser.add_argument("--report-output", default="")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--skip-report", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    smoke_script = (repo_root / "思忆集app" / "build" / "smoke_check.py").resolve()
    report_script = (repo_root / "scripts" / "generate_release_report.py").resolve()

    if not smoke_script.is_file():
        print(f"[desktop-gate] missing smoke script: {smoke_script}")
        return 2
    if not report_script.is_file():
        print(f"[desktop-gate] missing release report script: {report_script}")
        return 2

    channel = _resolve_default_channel(args.suite) if args.channel == "auto" else args.channel
    report_output = Path(args.report_output).expanduser().resolve() if args.report_output else (repo_root / "release_report.md").resolve()

    try:
        if not args.skip_smoke:
            _run_step(
                f"run desktop smoke suite={args.suite}",
                [args.python_executable, str(smoke_script), "--suite", args.suite],
                cwd=repo_root,
            )

        if not args.skip_report:
            command = [
                args.python_executable,
                str(report_script),
                "--channel",
                str(channel),
                "--build",
                str(args.build),
                "--rollback-version",
                str(args.rollback_version),
                "--output",
                str(report_output),
            ]
            if args.feature_changes_file:
                command.extend(["--feature-changes-file", str(Path(args.feature_changes_file).expanduser().resolve())])
            if args.known_issues_file:
                command.extend(["--known-issues-file", str(Path(args.known_issues_file).expanduser().resolve())])
            if args.rollback_steps_file:
                command.extend(["--rollback-steps-file", str(Path(args.rollback_steps_file).expanduser().resolve())])
            _run_step("generate release report", command, cwd=repo_root)
            print(f"[desktop-gate] release version={_build_release_version(channel, args.build)}")
    except subprocess.CalledProcessError as exc:
        print(f"[desktop-gate] failed with exit code {exc.returncode}")
        return int(exc.returncode)

    print("[desktop-gate] all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
