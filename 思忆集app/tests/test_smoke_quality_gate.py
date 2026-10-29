from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from repositories import LocalSettingsRepository
pytest.skip("ui 包 / PyQt6 旧桌面路径已退役移除（headless venv 有意不装 desktop extras），守卫对象不存在——本文件去留待统一决策", allow_module_level=True)
from build.smoke_check import (
    _assert_browser_bridge_release_policy,
    _assert_qt_font_assets_available,
    _assert_startup_interactive_gate,
)


def test_assert_startup_interactive_gate_accepts_sample_under_target():
    _assert_startup_interactive_gate({"startup_interactive_ms": [1200.0]}, 2.5)


def test_assert_startup_interactive_gate_rejects_sample_over_target():
    with pytest.raises(RuntimeError, match="first interactive exceeded target"):
        _assert_startup_interactive_gate({"startup_interactive_ms": [2600.0]}, 2.5)


def test_assert_qt_font_assets_available_requires_existing_dir(tmp_path):
    with pytest.raises(RuntimeError, match="Qt 字体目录缺失"):
        _assert_qt_font_assets_available(tmp_path / "missing-fonts")


def test_assert_browser_bridge_release_policy_enforces_stable_whitelist(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    _assert_browser_bridge_release_policy(settings_repo)
