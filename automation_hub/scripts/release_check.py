#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen


SMOKE_TARGETS = [
    "tests/test_api_auth_flow.py",
    "tests/test_api_execution_flow.py",
    "tests/test_local_agent_flow_smoke.py",
    "tests/test_api_audit_consistency.py",
    "tests/test_release_check.py",
    "tests/test_rollback_drill.py",
    "-q",
]

REGRESSION_TARGETS = [
    "tests",
    "-q",
]

FULL_TARGETS = [
    "tests",
    "-vv",
]

DEFAULT_LIVE_API_BASE = "http://127.0.0.1:30000"


def _build_release_version(mode: str) -> str:
    now = datetime.now()
    year, week, _ = now.isocalendar()
    if mode in {"regression", "full", "live", "live-worker"}:
        return f"{year}.{week}.0"
    return f"{now.year}.{now.month:02d}{now.day:02d}-dev.1"


def _emit_release_report(
    repo_root: Path,
    mode: str,
    output_path: str | None = None,
    desktop_gate_summary: str = "未启用",
    desktop_gate_detail: dict | None = None,
) -> Path:
    report_path = Path(output_path).expanduser().resolve() if output_path else (repo_root / "release_report.md").resolve()
    channel = "stable" if mode in {"regression", "full", "live", "live-worker"} else "dev"
    lines = [
        f"# 发布报告 {_build_release_version(mode)} ({channel})",
        "",
        f"发布日期: {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "## 功能变化",
        "- 本次发布已通过自动化校验门禁。",
        f"- 桌面端门禁：{desktop_gate_summary}",
        "",
    ]

    if desktop_gate_detail:
        lines.append("### 桌面端门禁详情")
        for check_name, check_result in desktop_gate_detail.items():
            status_line = f"- **{check_name}**: {'通过' if check_result.get('passed') else '未执行/失败'}"
            if check_result.get("output_lines"):
                status_line += "  \\n" + "  \\n".join(check_result["output_lines"])
            lines.append(status_line)
        lines.append("")

    lines += [
        "## 已知问题",
        "- 暂无新增阻断问题；请关注运行日志中的降级告警。",
        "",
        "## 回滚步骤",
        "1. 停止当前发布批次并冻结流量扩容。",
        "2. 回退到上一个稳定版本。",
        "3. 执行快照恢复并重启服务。",
        "4. 复核启动、同步、配置保存、布局应用链路。",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[release-check] release report written: {report_path}")
    return report_path


def _log_release_gate_audit(api_base: str, token: str, mode: str, result: str, meta: dict | None = None) -> None:
    """Log a release gate audit event via the API."""
    url = f"{api_base.rstrip('/')}/audit/events"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps({
        "event_type": "release.gate",
        "action": mode,
        "status": result,
        "resource_type": "release",
        "message": f"release gate {mode}: {result}",
        "meta": meta or {},
    }, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=5):
            pass
    except Exception:
        # Audit logging failure should not break the gate
        print("[release-check] warning: failed to log audit event (non-fatal)")


def _resolve_live_token_file(token_file: str | None) -> Path:
    if token_file:
        return Path(token_file).expanduser().resolve()
    return (Path(__file__).resolve().parents[1] / ".admin_token").resolve()


def _run_step(title: str, command: list[str], cwd: Path) -> None:
    print(f"[release-check] {title}")
    print(f"[release-check] command: {' '.join(command)}")
    subprocess.run(command, cwd=str(cwd), check=True)


def _resolve_pytest_args(mode: str, override_args: list[str] | None) -> list[str]:
    if override_args:
        return override_args
    if mode == "smoke":
        return SMOKE_TARGETS.copy()
    if mode == "full":
        return FULL_TARGETS.copy()
    return REGRESSION_TARGETS.copy()


def _resolve_live_command(repo_root: Path, python_executable: str, script_name: str, api_base: str | None, token_file: str | None, executor: str | None = None) -> list[str]:
    resolved_api_base = (api_base or DEFAULT_LIVE_API_BASE).rstrip("/")
    resolved_token_file = _resolve_live_token_file(token_file)

    command = [python_executable, str(repo_root / script_name), "--api-base", resolved_api_base]
    command.extend(["--token-file", str(resolved_token_file)])
    if executor:
        command.extend(["--executor", executor])
    return command


def _run_live_preflight(api_base: str | None, token_file: str | None) -> None:
    resolved_api_base = (api_base or DEFAULT_LIVE_API_BASE).rstrip("/")
    resolved_token_file = _resolve_live_token_file(token_file)

    print(f"[release-check] live preflight api: {resolved_api_base}")
    print(f"[release-check] live preflight token file: {resolved_token_file}")

    if not resolved_token_file.exists():
        raise RuntimeError(
            f"live preflight failed: token file not found: {resolved_token_file}. "
            "use --live-token-file or set AUTOMATION_HUB_TOKEN_FILE"
        )

    token = resolved_token_file.read_text(encoding="utf-8").lstrip("\ufeff").strip()
    if not token:
        raise RuntimeError(f"live preflight failed: token file is empty: {resolved_token_file}")

    health_url = f"{resolved_api_base}/health"
    try:
        with urlopen(health_url, timeout=5) as response:
            if int(response.status) != 200:
                raise RuntimeError(f"live preflight failed: GET {health_url} returned {response.status}")
    except HTTPError as exc:
        raise RuntimeError(f"live preflight failed: GET {health_url} returned {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"live preflight failed: cannot reach {health_url}: {exc.reason}") from exc

    auth_me_url = f"{resolved_api_base}/auth/me"
    auth_request = Request(auth_me_url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(auth_request, timeout=5) as response:
            if int(response.status) != 200:
                raise RuntimeError(f"live preflight failed: GET {auth_me_url} returned {response.status}")
    except HTTPError as exc:
        raise RuntimeError(
            f"live preflight failed: GET {auth_me_url} returned {exc.code}. "
            "check whether the token is valid and has not expired"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"live preflight failed: cannot reach {auth_me_url}: {exc.reason}") from exc

    print("[release-check] live preflight passed")


def _run_desktop_gate(repo_root: Path, python_executable: str, suite: str) -> dict[str, Any]:
    desktop_gate_script = (repo_root.parent / "scripts" / "desktop_quality_gate.py").resolve()
    if not desktop_gate_script.is_file():
        raise RuntimeError(f"desktop gate failed: gate script not found: {desktop_gate_script}")

    result = subprocess.run(
        [
            python_executable,
            str(desktop_gate_script),
            "--suite",
            suite,
            "--skip-report",
        ],
        cwd=repo_root.parent,
        capture_output=True,
        text=True,
    )

    detail: dict[str, Any] = {
        "script": str(desktop_gate_script),
        "suite": suite,
        "exit_code": result.returncode,
        "passed": result.returncode == 0,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
    }

    # Extract individual check results from output
    output_lines = (result.stdout or "").splitlines()
    for line in output_lines:
        if line.startswith("[desktop-gate] desktop gate summary:"):
            detail["summary_line"] = line.replace("[desktop-gate] desktop gate summary: ", "").strip()
        if "cold_start=" in line or "smoke=" in line or "doctor=" in line:
            for token in line.split(","):
                token = token.strip()
                if "=" in token:
                    key, val = token.split("=", 1)
                    detail[f"check_{key}"] = val.strip()

    if result.returncode != 0:
        print(f"[release-check] desktop gate stdout:\n{result.stdout}")
        print(f"[release-check] desktop gate stderr:\n{result.stderr}")
        raise subprocess.CalledProcessError(result.returncode, desktop_gate_script, output=result.stdout, stderr=result.stderr)

    return detail


def _should_run_desktop_gate(mode: str, with_desktop_gate: bool, skip_desktop_gate: bool) -> bool:
    if skip_desktop_gate:
        return False
    if with_desktop_gate:
        return True
    return mode in {"regression", "full", "live", "live-worker"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Automation Hub 发布前校验入口")
    parser.add_argument(
        "--mode",
        choices=["smoke", "regression", "full", "live", "live-worker"],
        default="regression",
        help="选择校验层级：smoke 为主链路快速校验，regression 为默认回归集，full 为详细输出全量回归，live 为连接真实 API 的 API 验收，live-worker 为连接真实 API 的 worker 消费验收。",
    )
    parser.add_argument(
        "--pytest-args",
        nargs="*",
        default=None,
        help="传给 pytest 的额外参数；设置后会覆盖 --mode 的默认目标。",
    )
    parser.add_argument("--live-api-base", default=None, help=f"live 模式下传给 verify_system.py 的 API 地址，默认 {DEFAULT_LIVE_API_BASE}")
    parser.add_argument("--live-token-file", default=None, help="live 模式下传给 verify_system.py 的 token 文件路径")
    parser.add_argument("--live-worker-executor", choices=["host", "docker"], default="host", help="live-worker 模式下要验证的执行器类型")
    parser.add_argument("--with-desktop-gate", action="store_true", help="额外执行思忆集桌面端 smoke_check 门禁")
    parser.add_argument("--skip-desktop-gate", action="store_true", help="跳过默认启用的桌面端 release 门禁")
    parser.add_argument(
        "--desktop-suite",
        choices=["core", "ui", "p2", "all", "release"],
        default="release",
        help="桌面端门禁套件，默认 release（core+ui+p2 全链路）",
    )
    parser.add_argument("--release-report", default="", help="发布报告输出路径（默认 automation-hub/release_report.md）")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    python_executable = sys.executable

    # Resolve live token for audit logging (only in live modes)
    live_token_audit: str | None = None
    if args.mode in {"live", "live-worker"}:
        try:
            live_token_audit = _resolve_live_token_file(args.live_token_file).read_text(encoding="utf-8").lstrip("﻿").strip()
        except Exception:
            pass

    try:
        if args.mode in {"live", "live-worker"}:
            _run_live_preflight(args.live_api_base, args.live_token_file)
            _log_release_gate_audit(
                args.live_api_base or DEFAULT_LIVE_API_BASE,
                live_token_audit,
                args.mode,
                "preflight_passed",
                meta={"mode": args.mode, "executor": args.live_worker_executor if args.mode == "live-worker" else None},
            )
            script_name = "verify_system.py" if args.mode == "live" else "verify_worker_system.py"
            title = "run live api verification" if args.mode == "live" else "run live worker verification"
            _run_step(
                title,
                _resolve_live_command(
                    repo_root,
                    python_executable,
                    script_name,
                    args.live_api_base,
                    args.live_token_file,
                    executor=args.live_worker_executor if args.mode == "live-worker" else None,
                ),
                cwd=repo_root,
            )
        else:
            pytest_args = _resolve_pytest_args(args.mode, args.pytest_args)
            _run_step(
                f"run {args.mode} tests",
                [python_executable, "-m", "pytest", *pytest_args],
                cwd=repo_root,
            )

        desktop_gate_summary = "未启用"
        desktop_gate_detail: dict | None = None
        if _should_run_desktop_gate(args.mode, args.with_desktop_gate, args.skip_desktop_gate):
            desktop_gate_detail = _run_desktop_gate(repo_root, python_executable, args.desktop_suite)
            desktop_gate_summary = f"通过（suite={args.desktop_suite}）"
    except subprocess.CalledProcessError as exc:
        print(f"[release-check] failed with exit code {exc.returncode}")
        _log_release_gate_audit(
            args.live_api_base or DEFAULT_LIVE_API_BASE,
            live_token_audit,
            args.mode,
            "failed",
            meta={"exit_code": exc.returncode},
        )
        return exc.returncode
    except RuntimeError as exc:
        print(f"[release-check] {exc}")
        _log_release_gate_audit(
            args.live_api_base or DEFAULT_LIVE_API_BASE,
            live_token_audit,
            args.mode,
            "failed",
            meta={"error": str(exc)},
        )
        return 2

    _emit_release_report(
        repo_root,
        args.mode,
        output_path=args.release_report or None,
        desktop_gate_summary=desktop_gate_summary,
        desktop_gate_detail=desktop_gate_detail,
    )
    _log_release_gate_audit(
        args.live_api_base or DEFAULT_LIVE_API_BASE,
        live_token_audit,
        args.mode,
        "passed",
        meta={"desktop_gate": desktop_gate_summary},
    )
    print("[release-check] all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())