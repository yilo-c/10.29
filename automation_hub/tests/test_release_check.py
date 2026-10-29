from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from release_check import _should_run_desktop_gate


def test_regression_mode_enables_desktop_gate_by_default():
    assert _should_run_desktop_gate("regression", False, False) is True


def test_smoke_mode_keeps_desktop_gate_optional():
    assert _should_run_desktop_gate("smoke", False, False) is False


def test_skip_desktop_gate_overrides_default_enablement():
    assert _should_run_desktop_gate("full", False, True) is False
