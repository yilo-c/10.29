"""P2-23/24/25 CI 集成回归测试。

验证：
1. coverage_gate.py 的 DEFAULT_THRESHOLD ≥ 20（P2-25 提升到 20%，由扩列后的
   stable-core 实测 ~22% 支撑）
2. dod_gate.py 的 --coverage-threshold 默认 ≥ 20
3. python-tests.yml 已引用 scan_secrets / check-layer-boundaries / coverage_gate
4. python-tests.yml 已声明 bandit + pip-audit 步骤
5. ai/_assistant_bridge.py 是 ai → assistant 的唯一桥点
6. scripts/check-layer-boundaries.py 在当前仓库返回 0
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT


def _read(rel: str) -> str:
    return (REPO_ROOT.parent / rel).read_text(encoding="utf-8")


# ── 阈值不回退 ─────────────────────────────────────────────────


def test_coverage_gate_threshold_at_least_20():
    src = _read("scripts/coverage_gate.py")
    m = re.search(r"DEFAULT_THRESHOLD\s*=\s*([\d.]+)", src)
    assert m, "DEFAULT_THRESHOLD constant not found"
    assert float(m.group(1)) >= 20.0, f"覆盖率阈值不应低于 20，实际 {m.group(1)}"


def test_dod_gate_coverage_threshold_at_least_20():
    src = _read("scripts/dod_gate.py")
    m = re.search(r"coverage_threshold:\s*float\s*=\s*([\d.]+)", src)
    assert m, "run_tests coverage_threshold default not found"
    assert float(m.group(1)) >= 20.0
    m2 = re.search(r'--coverage-threshold[\s\S]+?default=([\d.]+)', src)
    assert m2, "--coverage-threshold default not found"
    assert float(m2.group(1)) >= 20.0


# ── CI workflow 接入 ──────────────────────────────────────────


def test_python_tests_workflow_wires_quality_scripts():
    yml = _read(".github/workflows/python-tests.yml")
    assert "scripts/scan_secrets.py" in yml, "scan_secrets 未接入 CI"
    assert "scripts/check-layer-boundaries.py" in yml, "层边界检查未接入 CI"
    assert "scripts/coverage_gate.py" in yml, "coverage_gate 未接入 CI"


def test_python_tests_workflow_has_bandit_and_pip_audit():
    yml = _read(".github/workflows/python-tests.yml")
    assert "bandit" in yml.lower(), "bandit 安全扫描未声明"
    assert "pip-audit" in yml.lower(), "pip-audit 漏洞扫描未声明"


# ── 桥层就位 ───────────────────────────────────────────────────


def test_assistant_bridge_module_exists_and_exports():
    from ai import _assistant_bridge as bridge

    assert hasattr(bridge, "PrivacyClassifier")
    assert hasattr(bridge, "inject_xiaowen_persona")
    assert "PrivacyClassifier" in bridge.__all__
    assert "inject_xiaowen_persona" in bridge.__all__


def test_ai_client_does_not_import_assistant_directly():
    """ai/client/*.py 不得保留对 assistant 的直接 import（除非通过 bridge）。"""
    client_dir = APP_ROOT / "ai" / "client"
    offenders: list[str] = []
    for py in client_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.match(r"^(from|import)\s+assistant\b", stripped):
                offenders.append(f"{py.name}:{lineno} {stripped}")
    assert not offenders, "ai/client/ 直接 import assistant 应通过 ai._assistant_bridge 中转:\n" + "\n".join(offenders)


# ── 端到端：layer-boundaries 脚本本身通过 ──────────────────────


def test_layer_boundaries_script_passes():
    script = REPO_ROOT.parent / "scripts" / "check-layer-boundaries.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT.parent),
        timeout=60,
    )
    assert result.returncode == 0, (
        f"layer-boundaries 脚本失败:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
