"""Definition of Done 门禁脚本。

整合冒烟验收、覆盖率门禁/单元测试、冷启动稳定性三阶段检查，
输出统一 PASS/FAIL 判定和简要报告。

用法:
    python scripts/dod_gate.py                  # 完整门禁
    python scripts/dod_gate.py --quick          # 快速门禁（减少冷启动轮次）
    python scripts/dod_gate.py --skip-cold      # 跳过冷启动
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = WORKSPACE_ROOT / "思忆集app"
SCRIPTS_DIR = WORKSPACE_ROOT / "scripts"
TESTS_DIR = APP_DIR / "tests"
PYTHON = sys.executable
COVERAGE_GATE = SCRIPTS_DIR / "coverage_gate.py"


def _banner(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[int, str, float]:
    """执行子进程，返回 (exit_code, 合并输出, 耗时秒)。"""
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd or WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        elapsed = time.monotonic() - start
        combined = (result.stdout or "") + (result.stderr or "")
        return result.returncode, combined, elapsed
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT", time.monotonic() - start


# ── 阶段 1: 冒烟验收 ────────────────────────────────────────

def run_smoke(suite: str = "release") -> dict:
    _banner(f"阶段 1: 冒烟验收 (suite={suite})")
    cmd = [PYTHON, str(APP_DIR / "smoke_check.py"), "--suite", suite]
    code, output, elapsed = _run(cmd, cwd=APP_DIR, timeout=180)
    passed = code == 0 and "SMOKE_FAILED" not in output
    status = "PASS" if passed else "FAIL"
    print(f"  冒烟验收 {status}  (exit={code}, {elapsed:.1f}s)")
    if not passed:
        # 只打印最后 40 行
        tail = "\n".join(output.splitlines()[-40:])
        print(tail)
    return {"stage": "smoke", "status": status, "exit_code": code, "elapsed": elapsed}


# ── 阶段 2: 单元测试 ────────────────────────────────────────

def run_tests(*, legacy_pytest: bool = False, coverage_threshold: float = 20.0) -> dict:
    stage_title = "阶段 2: 单元测试 (pytest)" if legacy_pytest else "阶段 2: 核心覆盖率门禁"
    _banner(stage_title)
    if not TESTS_DIR.is_dir():
        print("  tests 目录不存在，跳过")
        return {"stage": "tests", "status": "SKIP", "exit_code": -1, "elapsed": 0}

    if legacy_pytest:
        cmd = [PYTHON, "-m", "pytest", str(TESTS_DIR), "-q", "--tb=short", "--no-header"]
    else:
        if not COVERAGE_GATE.is_file():
            print("  coverage_gate.py 不存在，跳过")
            return {"stage": "tests", "status": "SKIP", "exit_code": -1, "elapsed": 0}
        cmd = [PYTHON, str(COVERAGE_GATE), "--threshold", str(coverage_threshold)]
    code, output, elapsed = _run(cmd, cwd=APP_DIR, timeout=120)
    passed = code == 0
    status = "PASS" if passed else "FAIL"

    # 提取 pytest 摘要行
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped and ("passed" in stripped or "failed" in stripped or "error" in stripped):
            print(f"  {stripped}")
            break

    label = "单元测试" if legacy_pytest else "覆盖率门禁"
    print(f"  {label} {status}  (exit={code}, {elapsed:.1f}s)")
    if not passed:
        tail = "\n".join(output.splitlines()[-30:])
        print(tail)
    return {"stage": "tests", "status": status, "exit_code": code, "elapsed": elapsed}


# ── 阶段 3: 冷启动稳定性 ────────────────────────────────────

def run_cold_start(rounds: int = 5, auto_exit_ms: int = 1200, timeout_sec: int = 20) -> dict:
    _banner(f"阶段 3: 冷启动稳定性 ({rounds} 轮)")
    cold_script = SCRIPTS_DIR / "cold_start_stability_check.py"
    if not cold_script.is_file():
        print("  冷启动脚本不存在，跳过")
        return {"stage": "cold_start", "status": "SKIP", "exit_code": -1, "elapsed": 0}

    cmd = [
        PYTHON, str(cold_script),
        "--rounds", str(rounds),
        "--auto-exit-ms", str(auto_exit_ms),
        "--timeout-sec", str(timeout_sec),
    ]
    total_timeout = rounds * (timeout_sec + 5) + 30
    code, output, elapsed = _run(cmd, timeout=total_timeout)
    passed = code == 0 and "COLD_START_OK" in output
    status = "PASS" if passed else "FAIL"
    print(f"  冷启动稳定性 {status}  (exit={code}, {elapsed:.1f}s)")
    if not passed:
        tail = "\n".join(output.splitlines()[-20:])
        print(tail)
    return {"stage": "cold_start", "status": status, "exit_code": code, "elapsed": elapsed}


# ── 门禁汇总 ────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="思忆集 Definition of Done 门禁")
    parser.add_argument("--quick", action="store_true", help="快速模式: core 冒烟 + 3 轮冷启动")
    parser.add_argument("--skip-cold", action="store_true", help="跳过冷启动阶段")
    parser.add_argument("--skip-smoke", action="store_true", help="跳过冒烟阶段")
    parser.add_argument("--skip-tests", action="store_true", help="跳过单元测试阶段")
    parser.add_argument("--cold-rounds", type=int, default=None, help="冷启动轮次 (默认: 5, quick 模式: 3)")
    parser.add_argument("--legacy-pytest", action="store_true", help="阶段 2 使用旧的全量 pytest，而不是稳定核心覆盖率门禁")
    parser.add_argument("--coverage-threshold", type=float, default=20.0, help="覆盖率门禁阈值，默认 20")
    args = parser.parse_args()

    start_time = time.monotonic()
    results: list[dict] = []

    # 阶段 1
    if not args.skip_smoke:
        suite = "core" if args.quick else "release"
        results.append(run_smoke(suite))
    else:
        results.append({"stage": "smoke", "status": "SKIP", "exit_code": -1, "elapsed": 0})

    # 阶段 2
    if not args.skip_tests:
        results.append(
            run_tests(
                legacy_pytest=args.legacy_pytest,
                coverage_threshold=args.coverage_threshold,
            )
        )
    else:
        results.append({"stage": "tests", "status": "SKIP", "exit_code": -1, "elapsed": 0})

    # 阶段 3
    if not args.skip_cold:
        rounds = args.cold_rounds or (3 if args.quick else 5)
        results.append(run_cold_start(rounds=rounds))
    else:
        results.append({"stage": "cold_start", "status": "SKIP", "exit_code": -1, "elapsed": 0})

    # 汇总
    total_elapsed = time.monotonic() - start_time
    _banner("DOD 门禁报告")
    all_ok = True
    for r in results:
        marker = "✓" if r["status"] == "PASS" else ("—" if r["status"] == "SKIP" else "✗")
        print(f"  {marker} {r['stage']:<14} {r['status']:<6} ({r['elapsed']:.1f}s)")
        if r["status"] == "FAIL":
            all_ok = False

    verdict = "PASS" if all_ok else "FAIL"
    print(f"\n  DOD_GATE_VERDICT={verdict}  total={total_elapsed:.1f}s  at={datetime.now().isoformat()}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
