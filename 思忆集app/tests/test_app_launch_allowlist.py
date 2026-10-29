"""回归测试：应用启动白名单的合并语义（2026-09-11，B1 落地）。

背景：``services/app_launch.APP_LAUNCH_ALLOWLIST`` 此前是纯硬编码 frozenset，
用户想启动名单外的应用（如宿主 WorkBuddy）无任何途径——"有引擎无钥匙孔"。

现改为：生效名单 = 代码默认集 ∪ settings 追加项
（``assistant_app_launch_extra_allowlist``），与 action_guard 白名单同一哲学。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from repositories import LocalSettingsRepository
from services.app_launch import (
    APP_LAUNCH_ALLOWLIST,
    SETTINGS_KEY_EXTRA_ALLOWLIST,
    resolve_app_launch_allowlist,
)


def _repo(tmp_path, extra):
    data_store = LocalDataStore(base_dir=str(tmp_path / "runtime"))
    repo = LocalSettingsRepository(data_store)
    if extra is not None:
        repo.set_setting(SETTINGS_KEY_EXTRA_ALLOWLIST, list(extra))
    return repo


def test_workbuddy_in_default_allowlist():
    """宿主应用在默认名单中（「每天自动打开 WorkBuddy」的既定需求）。"""
    assert "workbuddy" in APP_LAUNCH_ALLOWLIST
    assert "workbuddy" in resolve_app_launch_allowlist(None)


def test_extra_allowlist_merges_with_defaults(tmp_path):
    """settings 追加项与默认集合并（不覆盖）。"""
    repo = _repo(tmp_path, extra=["my-custom-app", "WorkBuddy"])
    allowlist = resolve_app_launch_allowlist(repo)
    assert "my-custom-app" in allowlist
    assert "workbuddy" in allowlist
    assert "chrome" in allowlist  # 默认项保留


def test_normalize_entries_accepts_comma_string():
    """归一函数兼容逗号分隔字符串（repo 层存 list 为主路径）。"""
    from services.app_launch import _normalize_entries

    assert _normalize_entries("foo-app, bar-app") == {"foo-app", "bar-app"}


def test_missing_repo_falls_back_to_defaults(tmp_path):
    """settings 仓储缺失/为空时回退默认集（不放大权限也不误锁）。"""
    allowlist = resolve_app_launch_allowlist(None)
    assert allowlist == set(APP_LAUNCH_ALLOWLIST)


def test_extra_allowlist_entries_are_case_insensitive(tmp_path):
    """追加项归一为小写（app_key 提取时也 lower，两侧一致）。"""
    repo = _repo(tmp_path, extra=["MyApp"])
    allowlist = resolve_app_launch_allowlist(repo)
    assert "myapp" in allowlist


def test_shell_app_launch_accepts_workbuddy(tmp_path, monkeypatch):
    """端到端（进程内）：_shell.app_launch 对 workbuddy 不再报 allowlist 拦截。

    用 monkeypatch 截断 Popen/startfile，只验证「名单检查通过、走到启动分支」。
    """
    import subprocess

    from assistant.capability._shell import ShellCapabilityDomain

    data_store = LocalDataStore(base_dir=str(tmp_path / "runtime"))
    settings_repo = LocalSettingsRepository(data_store)
    router = SimpleNamespace(
        settings_repository=settings_repo,
        _launched_processes={},
        # resolve_app_executable 走 environment_probe；返回 None 则 Popen 直接用命令名
        environment_probe=SimpleNamespace(resolve_app_executable=lambda name: None),
    )
    domain = ShellCapabilityDomain(router)

    captured: dict = {}

    def _fake_popen(cmd_parts, **kwargs):
        captured["cmd"] = list(cmd_parts)
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(domain.__class__.__module__ + ".subprocess.Popen", _fake_popen)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    result = domain.app_launch({"command": "workbuddy"})
    # 名单检查通过（未返回 blocked），走到了启动分支
    assert "not in the allowed applications list" not in str(result.get("reason", ""))
    assert result.get("launched") is True or captured.get("cmd")
