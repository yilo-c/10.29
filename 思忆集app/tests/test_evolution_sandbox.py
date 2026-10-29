from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from evolution.tech_radar import LEVEL_CONFIG, RadarFinding
from evolution.sandbox import (
    EvolutionSandbox,
    SandboxTestResult,
    _probe_import_subprocess,
    create_isolated_venv,
    pip_install_in_venv,
    run_isolated_install_probe,
)


def _make_sandbox():
    return EvolutionSandbox()


def _make_finding():
    return RadarFinding(
        name="chromadb",
        current_version="0.4.0",
        latest_version="0.6.0",
        source="pypi",
        category="dependency",
        level=LEVEL_CONFIG,
    )


def test_run_tests_passes():
    sandbox = _make_sandbox()
    finding = _make_finding()
    run = sandbox.run_tests(finding)
    assert run.overall_status == "passed"
    assert len(run.tests) == 3  # 3 个默认测试
    assert run.run_id is not None
    assert "通过" in run.summary


def test_run_tests_has_correct_target():
    sandbox = _make_sandbox()
    finding = _make_finding()
    run = sandbox.run_tests(finding)
    assert run.target_name == "chromadb"
    assert run.target_version == "0.6.0"


def test_get_run():
    sandbox = _make_sandbox()
    finding = _make_finding()
    run = sandbox.run_tests(finding)
    fetched = sandbox.get_run(run.run_id)
    assert fetched is not None
    assert fetched.run_id == run.run_id


def test_get_recent_runs():
    sandbox = _make_sandbox()
    sandbox.run_tests(_make_finding())
    recent = sandbox.get_recent_runs()
    assert len(recent) == 1


def test_sandbox_test_result_defaults():
    r = SandboxTestResult(test_name="import_test")
    assert r.status == "pending"
    assert r.output == ""
    assert r.error == ""
    assert r.duration_seconds == 0.0


def test_status_snapshot():
    sandbox = _make_sandbox()
    sandbox.run_tests(_make_finding())
    snapshot = sandbox.get_status_snapshot()
    assert snapshot["total_runs"] == 1
    assert snapshot["passed"] == 1
    assert snapshot["failed"] == 0


# ------------------------- 子进程隔离 -------------------------


def test_probe_import_known_good_module():
    """已安装的模块应返回 ok=True"""
    result = _probe_import_subprocess("sys")
    assert result["ok"] is True
    assert "version" in result


def test_probe_import_missing_module():
    """缺失模块应返回 ImportError 原因"""
    result = _probe_import_subprocess("nonexistent_pkg_xyz_999_zzz")
    assert result["ok"] is False
    assert result["reason"] == "ImportError"


def test_probe_import_invalid_empty_name():
    """空包名应被拒绝"""
    result = _probe_import_subprocess("")
    assert result["ok"] is False
    assert result["reason"] == "invalid_pkg_name"


def test_probe_import_invalid_name_with_space():
    """带空格的包名应被拒绝（防注入）"""
    result = _probe_import_subprocess("bad name with spaces")
    assert result["ok"] is False
    assert result["reason"] == "invalid_pkg_name"


def test_probe_import_with_members():
    """include_members=True 应返回 member_count"""
    result = _probe_import_subprocess("json", include_members=True)
    assert result["ok"] is True
    assert result.get("member_count", 0) > 0


def test_probe_import_timeout_handled():
    """子进程超时应映射为 reason=timeout"""

    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0] if args else "", timeout=1.0)

    with patch("evolution.sandbox.subprocess.run", side_effect=_fake_run):
        result = _probe_import_subprocess("anything_valid")
    assert result["ok"] is False
    assert result["reason"] == "timeout"


# ------------------------- C3-1b: venv 隔离 -------------------------


def _mk_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.stdout = stdout
    mock.stderr = stderr
    mock.returncode = returncode
    return mock


def test_create_isolated_venv_success(tmp_path):
    """成功路径：venv 创建 + pip 可用"""
    # 模拟 venv 创建后产生 python 可执行文件
    fake_calls = []

    def _fake_run(cmd, **kwargs):
        fake_calls.append(cmd)
        if "venv" in cmd:
            # 真实建一个含 python 文件的假 venv 目录
            venv_dir = Path(cmd[-1])
            bin_name = "Scripts" if os.name == "nt" else "bin"
            (venv_dir / bin_name).mkdir(parents=True, exist_ok=True)
            py_file = venv_dir / bin_name / ("python.exe" if os.name == "nt" else "python")
            py_file.write_text("")
            return _mk_completed(returncode=0)
        if "pip" in cmd and "--version" in cmd:
            return _mk_completed(stdout="pip 24.0", returncode=0)
        return _mk_completed(returncode=0)

    with patch("evolution.sandbox.subprocess.run", side_effect=_fake_run):
        result = create_isolated_venv(parent_dir=str(tmp_path))

    assert result["ok"] is True, result
    assert result["venv_dir"]
    assert result["python"]
    assert Path(result["python"]).exists()


def test_create_isolated_venv_failure(tmp_path):
    """venv 创建失败应返回 reason=create_failed"""
    with patch("evolution.sandbox.subprocess.run", return_value=_mk_completed(stderr="boom", returncode=1)):
        result = create_isolated_venv(parent_dir=str(tmp_path))
    assert result["ok"] is False
    assert result["reason"] == "create_failed"


def test_create_isolated_venv_timeout(tmp_path):
    """venv 创建超时应被映射"""

    def _fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=60)

    with patch("evolution.sandbox.subprocess.run", side_effect=_fake_run):
        result = create_isolated_venv(parent_dir=str(tmp_path))
    assert result["ok"] is False
    assert result["reason"] == "timeout"


def test_pip_install_rejects_shell_metachars():
    """非法字符（shell 元字符）应被拒绝，不调用 subprocess"""
    with patch("evolution.sandbox.subprocess.run") as mocked:
        result = pip_install_in_venv("python", "evilpkg; rm -rf /")
    assert result["ok"] is False
    assert result["reason"] == "invalid_spec"
    mocked.assert_not_called()


def test_pip_install_empty_spec():
    result = pip_install_in_venv("python", "")
    assert result["ok"] is False
    assert result["reason"] == "invalid_spec"


def test_pip_install_success():
    with patch(
        "evolution.sandbox.subprocess.run",
        return_value=_mk_completed(stdout="Successfully installed foo-1.0", returncode=0),
    ):
        result = pip_install_in_venv("python", "foo==1.0")
    assert result["ok"] is True
    assert "Successfully" in result["stdout"]


def test_pip_install_failure():
    with patch(
        "evolution.sandbox.subprocess.run",
        return_value=_mk_completed(stderr="No matching distribution", returncode=1),
    ):
        result = pip_install_in_venv("python", "ghostpkg==9.9")
    assert result["ok"] is False
    assert result["reason"] == "install_failed"
    assert "No matching" in result["error"]


def test_pip_install_timeout():
    def _fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)

    with patch("evolution.sandbox.subprocess.run", side_effect=_fake_run):
        result = pip_install_in_venv("python", "slowpkg")
    assert result["ok"] is False
    assert result["reason"] == "timeout"


def test_run_isolated_install_probe_invalid_name():
    result = run_isolated_install_probe("bad name!!")
    assert result["ok"] is False
    assert result["stage"] == "venv"


def test_run_isolated_install_probe_full_success(tmp_path):
    """整条流水线：venv ok → install ok → probe ok"""

    def _fake_run(cmd, **kwargs):
        # venv 创建
        if "venv" in cmd and "-m" in cmd:
            venv_dir = Path(cmd[-1])
            bin_name = "Scripts" if os.name == "nt" else "bin"
            (venv_dir / bin_name).mkdir(parents=True, exist_ok=True)
            py = venv_dir / bin_name / ("python.exe" if os.name == "nt" else "python")
            py.write_text("")
            return _mk_completed(returncode=0)
        # pip --version
        if "pip" in cmd and "--version" in cmd:
            return _mk_completed(stdout="pip 24.0", returncode=0)
        # pip install
        if "pip" in cmd and "install" in cmd:
            return _mk_completed(stdout="Successfully installed", returncode=0)
        # probe 子进程
        if "-c" in cmd:
            return _mk_completed(stdout='{"ok": true, "version": "1.2.3"}', returncode=0)
        return _mk_completed(returncode=0)

    with patch("evolution.sandbox.subprocess.run", side_effect=_fake_run):
        result = run_isolated_install_probe(
            "samplepkg", version="1.2.3", parent_dir=str(tmp_path), keep_venv=True
        )

    assert result["ok"] is True, result
    assert result["stage"] == "probe"
    assert result["probe"]["ok"] is True
    assert result["probe"]["version"] == "1.2.3"


def test_run_isolated_install_probe_install_fails(tmp_path):
    """install 阶段失败应短路在 stage=install"""

    def _fake_run(cmd, **kwargs):
        if "venv" in cmd and "-m" in cmd:
            venv_dir = Path(cmd[-1])
            bin_name = "Scripts" if os.name == "nt" else "bin"
            (venv_dir / bin_name).mkdir(parents=True, exist_ok=True)
            py = venv_dir / bin_name / ("python.exe" if os.name == "nt" else "python")
            py.write_text("")
            return _mk_completed(returncode=0)
        if "pip" in cmd and "--version" in cmd:
            return _mk_completed(returncode=0)
        if "pip" in cmd and "install" in cmd:
            return _mk_completed(stderr="No distribution found", returncode=1)
        return _mk_completed(returncode=0)

    with patch("evolution.sandbox.subprocess.run", side_effect=_fake_run):
        result = run_isolated_install_probe("ghostpkg", parent_dir=str(tmp_path))

    assert result["ok"] is False
    assert result["stage"] == "install"
    assert result["reason"] == "install_failed"


def test_run_isolated_install_probe_probe_fails(tmp_path):
    """probe 阶段失败：install 通了但 import 不到"""

    def _fake_run(cmd, **kwargs):
        if "venv" in cmd and "-m" in cmd:
            venv_dir = Path(cmd[-1])
            bin_name = "Scripts" if os.name == "nt" else "bin"
            (venv_dir / bin_name).mkdir(parents=True, exist_ok=True)
            py = venv_dir / bin_name / ("python.exe" if os.name == "nt" else "python")
            py.write_text("")
            return _mk_completed(returncode=0)
        if "pip" in cmd and "--version" in cmd:
            return _mk_completed(returncode=0)
        if "pip" in cmd and "install" in cmd:
            return _mk_completed(returncode=0)
        if "-c" in cmd:
            return _mk_completed(
                stdout='{"ok": false, "reason": "ImportError", "error": "no module"}',
                returncode=0,
            )
        return _mk_completed(returncode=0)

    with patch("evolution.sandbox.subprocess.run", side_effect=_fake_run):
        result = run_isolated_install_probe("weirdpkg", parent_dir=str(tmp_path))

    assert result["ok"] is False
    assert result["stage"] == "probe"
    assert result["probe"]["reason"] == "ImportError"


def test_run_isolated_install_probe_cleans_up_by_default(tmp_path):
    """keep_venv=False（默认）应清理 venv 目录"""
    captured_dir: dict[str, str] = {}

    def _fake_run(cmd, **kwargs):
        if "venv" in cmd and "-m" in cmd:
            venv_dir = Path(cmd[-1])
            bin_name = "Scripts" if os.name == "nt" else "bin"
            (venv_dir / bin_name).mkdir(parents=True, exist_ok=True)
            py = venv_dir / bin_name / ("python.exe" if os.name == "nt" else "python")
            py.write_text("")
            captured_dir["d"] = str(venv_dir)
            return _mk_completed(returncode=0)
        if "pip" in cmd and "--version" in cmd:
            return _mk_completed(returncode=0)
        if "pip" in cmd and "install" in cmd:
            return _mk_completed(returncode=0)
        if "-c" in cmd:
            return _mk_completed(stdout='{"ok": true}', returncode=0)
        return _mk_completed(returncode=0)

    with patch("evolution.sandbox.subprocess.run", side_effect=_fake_run):
        run_isolated_install_probe("okpkg", parent_dir=str(tmp_path))

    assert captured_dir, "venv 目录应被创建过"
    assert not Path(captured_dir["d"]).exists(), "默认应清理 venv"


# ------------------------- 端到端慢测（默认跳过）-------------------------

import pytest

SLOW_FLAG = os.environ.get("SIYI_RUN_SLOW_VENV") == "1"


@pytest.mark.skipif(not SLOW_FLAG, reason="慢测：set SIYI_RUN_SLOW_VENV=1 启用")
def test_run_isolated_install_probe_real_pip(tmp_path):
    """真实跑一次：建 venv + 装 packaging（小包） + probe import。"""
    result = run_isolated_install_probe(
        "packaging", parent_dir=str(tmp_path), keep_venv=False
    )
    assert result["ok"] is True, result
    assert result["stage"] == "probe"
    assert result["probe"]["ok"] is True
