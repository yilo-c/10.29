"""M4a-3：kernel_drill 计划生成测试（副本实跑=🔒，本节只锁计划面）。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from scripts.kernel_drill import build_plan, render_plan  # noqa: E402


def test_plan_contains_purge_and_assert_sections():
    plan = build_plan("data/data", "data/data-drill-copy")
    titles = [title for title, _steps in plan]
    assert len(plan) == 2
    assert any("置废" in t for t in titles)
    assert any("断言" in t for t in titles)


def test_plan_steps_reference_fatal_marker_source():
    rendered = render_plan(build_plan("data/data", "data/data-drill-copy"))
    assert "_FATAL_KERNEL_ERROR_MARKERS" in rendered
    assert "run_gateway_path" in rendered
    assert "SIYI_DATA_DIR" in rendered


def test_plan_never_writes_production_dir():
    _title, steps = build_plan("data/data", "data/data-drill-copy")[0]
    assert all("data/data-drill-copy" in s or "禁碰生产" in s or "只写副本" in s or "副本" in s for s in steps[1:])


def test_dry_run_cli_exits_zero_and_prints_plan():
    proc = subprocess.run(
        [sys.executable, str(APP_ROOT / "scripts" / "kernel_drill.py"), "--dry-run"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(APP_ROOT),
        timeout=60,
    )
    assert proc.returncode == 0
    assert "置废计划" in proc.stdout
    assert "断言计划" in proc.stdout
    assert "DRY-RUN" in proc.stdout
