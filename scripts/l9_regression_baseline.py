#!/usr/bin/env python
"""L9 回归：定义「关键回归集」并生成全量 pytest 基线报告。

分层计划（docs/test-observation-plan.md §1）：
    L9 = 关键回归集（domain models + method_registry + providers + M4 定向）
         + 全量 pytest 基线报告

用法：
    python scripts/l9_regression_baseline.py [--full] [--output-dir acceptance_output]

产出：
    acceptance_output/l9_critical_baseline.json  关键回归集逐文件/用例结果
    acceptance_output/l9_full_baseline.json      全量基线（--full 时）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── 关键回归集（相对 <repo>/思忆集app 的路径）──────────────────────────
# domain models / schema 契约
DOMAIN_SCHEMA = [
    "tests/test_memory_entry_schema.py",
    "tests/test_capability_schema_fix.py",
    "tests/test_settings_schema_migration.py",
    "tests/test_mobile_desktop_schema_parity.py",
]
# method_registry（registry + schema 校验）
METHOD_REGISTRY = [
    "tests/test_capability_schema_fix.py",
]
# providers（路由 / 注册 / 拓扑 / OAuth）
PROVIDERS = [
    "tests/test_ai_service_provider_routing.py",
    "tests/test_api_provider_registry.py",
    "tests/test_llm_provider_topology.py",
    "tests/test_provider_routing.py",
    "tests/test_youdao_oauth_provider.py",
    "tests/core/test_provider_config.py",
]
# M4 定向（kernel 内核路由 + skill cards + 网关契约）
M4_TARGETED = [
    "tests/test_kernel_status.py",
    "tests/test_agent_gateway.py",
    "tests/test_agent_gateway_contract.py",
    "tests/test_cron_actions.py",
    "tests/test_assistant_checkpoint.py",
    "tests/test_butler_checkpoint.py",
]

CRITICAL_REGRESSION = sorted(
    set(DOMAIN_SCHEMA + METHOD_REGISTRY + PROVIDERS + M4_TARGETED)
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
APP_DIR = REPO_ROOT / "思忆集app"


def _default_venv_python() -> str:
    """优先项目 venv 的 python，否则用当前解释器。"""
    for cand in (
        REPO_ROOT / ".venv3.11" / "Scripts" / "python.exe",
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
    ):
        if cand.is_file():
            return str(cand)
    return sys.executable


def _run_pytest(
    python: str, targets: list[str], junit_xml: Path, *, cwd: Path = APP_DIR,
    num_workers: int = 0,
) -> dict[str, Any]:
    """运行 pytest 并把结果归一为基线 dict。

    automation_hub 测试独立于 思忆集app/tests（有自己的 conftest + SQLAlchemy），
    需以仓库根为 cwd 运行。num_workers>1 时经 pytest-xdist 并行（-n），
    可把全量套件从 >45min 降到 ~10-15min。
    """
    started = datetime.now(timezone.utc)
    cmd = [
        python, "-m", "pytest", *targets,
        "-q", "--no-header", "-p", "no:cacheprovider",
        "--junitxml", str(junit_xml),
    ]
    if num_workers > 1:
        cmd += ["-n", str(num_workers)]
    # 全量套件（3830 用例含集成/并发/子进程类）实测 >45 分钟；离线后端
    # 未根本改变（套件体量决定）。设 3600s（60min）上限——串行可靠但慢，
    # 需快速时另跑 `--workers N`（xdist，前提：集成测试已重构为可并行）。
    completed = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=3600,
    )
    finished = datetime.now(timezone.utc)
    return {
        "exit_code": completed.returncode,
        "passed": completed.returncode == 0,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "stdout_tail": "\n".join((completed.stdout or "").splitlines()[-25:]),
        "stderr_tail": "\n".join((completed.stderr or "").splitlines()[-15:]),
    }


def _parse_junit(junit_xml: Path) -> dict[str, Any]:
    """从 --junitxml 结果解析用例级统计（无 junit 解析库时兜底）。"""
    text = junit_xml.read_text(encoding="utf-8", errors="replace")
    total = text.count("<testcase ")
    failed = text.count('<failure ')
    errors = text.count('<error ')
    skipped = text.count('<skipped ')
    return {
        "tests": total,
        "failures": failed,
        "errors": errors,
        "skipped": skipped,
        "passed": max(0, total - failed - errors - skipped),
    }


def _parse_summary_line(stdout_tail: str) -> dict[str, int]:
    """解析 pytest -q 汇总行（如 "123 passed, 2 failed in 5.0s"）。"""
    out: dict[str, int] = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0, "xfailed": 0}
    for key in out:
        match = re.search(rf"(\d+)\s+{key}\b", stdout_tail)
        if match:
            out[key] = int(match.group(1))
    return out


def _baseline_report(name: str, targets: list[str], result: dict[str, Any]) -> dict[str, Any]:
    summary = _parse_summary_line(result["stdout_tail"])
    report: dict[str, Any] = {
        "layer": "L9",
        "name": name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_files": targets,
        "target_file_count": len(targets),
        "result": result,
        "summary": summary,
    }
    junit_stats = result.get("junit_stats")
    if junit_stats is not None:
        report["junit_stats"] = junit_stats
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="L9 关键回归集 + 全量基线")
    parser.add_argument("--full", action="store_true", help="同时运行全量测试套件基线")
    parser.add_argument("--workers", type=int, default=0,
                        help="全量基线的 pytest-xdist 并行 worker 数（默认 0=串行。"
                             "注意：xdist 与依赖会话级后端的集成测试不兼容——直接 WS 测试需 autostart、"
                             "临时后端 WS 就绪滞后 TCP、多 worker 多后端资源重，需先重构集成测试再启用）")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "acceptance_output"))
    parser.add_argument("--python", default=_default_venv_python())
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    python = args.python

    reports: list[dict[str, Any]] = []

    # 1. 关键回归集
    junit_critical = output_dir / "l9_critical_junit.xml"
    print(f"[l9] 关键回归集：{len(CRITICAL_REGRESSION)} 个文件")
    res = _run_pytest(python, CRITICAL_REGRESSION, junit_critical)
    res["junit_stats"] = _parse_junit(junit_critical)
    critical_report = _baseline_report("critical_regression", CRITICAL_REGRESSION, res)
    reports.append(critical_report)
    print(
        f"[l9] critical: {critical_report['summary']} "
        f"exit={res['exit_code']} duration={res['duration_seconds']}s"
    )

    # 2. 全量基线（--full）：思忆集app/tests + automation_hub/tests（分根）
    if args.full:
        junit_full = output_dir / "l9_full_junit.xml"
        print(f"[l9] 全量基线：tests/（workers={args.workers}）")
        res_full = _run_pytest(python, ["tests/"], junit_full, num_workers=args.workers)
        res_full["junit_stats"] = _parse_junit(junit_full)
        full_report = _baseline_report("full_baseline", ["tests/"], res_full)
        reports.append(full_report)
        print(
            f"[l9] full: {full_report['summary']} "
            f"exit={res_full['exit_code']} duration={res_full['duration_seconds']}s"
        )

        junit_hub = output_dir / "l9_automation_hub_junit.xml"
        print("[l9] 全量基线：automation_hub/tests/（cwd=仓库根）")
        res_hub = _run_pytest(python, ["automation_hub/tests/"], junit_hub, cwd=REPO_ROOT,
                              num_workers=args.workers)
        res_hub["junit_stats"] = _parse_junit(junit_hub)
        hub_report = _baseline_report("full_baseline_automation_hub", ["automation_hub/tests/"], res_hub)
        reports.append(hub_report)
        print(
            f"[l9] automation_hub: {hub_report['summary']} "
            f"exit={res_hub['exit_code']} duration={res_hub['duration_seconds']}s"
        )

    out_path = output_dir / "l9_baseline.json"
    out_path.write_text(
        json.dumps({"reports": reports}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[l9] 基线报告已写入 {out_path}")
    return 0 if all(r["result"]["passed"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
