"""ShellExecutionModel 单元测试。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "思忆集app"))
from core.shell_execution import ShellExecutionModel


# ── build_command ─────────────────────────────────────────


class TestBuildCommand:
    def test_empty_command_raises(self):
        model = ShellExecutionModel()
        with pytest.raises(ValueError, match="empty_shell_command"):
            model.build_command("")

    def test_command_with_args_list(self):
        model = ShellExecutionModel()
        with patch("shutil.which", return_value="/usr/bin/git"):
            parts = model.build_command("git", args=["status", "--short"])
        assert parts[-2:] == ["status", "--short"]

    def test_command_with_empty_args_ignored(self):
        model = ShellExecutionModel()
        with patch("shutil.which", return_value="/usr/bin/myapp"):
            parts = model.build_command("myapp", args=["hello", "", "  "])
        assert "hello" in parts
        assert "" not in parts[1:]

    @pytest.mark.skipif(os.name != "nt", reason="Windows-only CMD builtin routing")
    def test_cmd_builtin_wrapped_in_cmd_exe(self):
        model = ShellExecutionModel()
        parts = model.build_command("dir", args=["C:\\"])
        assert parts[0] == "cmd.exe"
        assert "/c" in parts

    def test_executable_not_found_raises(self):
        model = ShellExecutionModel()
        with patch("shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError):
                model.build_command("nonexistent_binary_xyz")


# ── resolve_working_dir ───────────────────────────────────


class TestResolveWorkingDir:
    def test_no_workspace_no_dir_returns_none(self):
        model = ShellExecutionModel()
        assert model.resolve_working_dir() is None

    def test_workspace_root_used_as_default(self, tmp_path):
        model = ShellExecutionModel(workspace_root=tmp_path)
        assert model.resolve_working_dir() == str(tmp_path)

    def test_relative_dir_resolved_under_workspace(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        model = ShellExecutionModel(workspace_root=tmp_path)
        result = model.resolve_working_dir("sub")
        assert result == str(sub)

    def test_outside_workspace_raises(self, tmp_path):
        model = ShellExecutionModel(workspace_root=tmp_path / "project")
        with pytest.raises(ValueError, match="working_dir_outside_workspace"):
            model.resolve_working_dir(str(tmp_path / "other"))


# ── build_environment ─────────────────────────────────────


class TestBuildEnvironment:
    def test_denylist_keys_excluded(self):
        model = ShellExecutionModel()
        with patch.dict(os.environ, {"PYTHONPATH": "/bad", "HOME": "/home/user"}):
            env = model.build_environment()
        assert "PYTHONPATH" not in env

    def test_allowlist_keys_injected(self):
        model = ShellExecutionModel()
        env = model.build_environment({"FORCE_COLOR": "1", "HACK_KEY": "x"})
        assert env.get("FORCE_COLOR") == "1"
        assert "HACK_KEY" not in env

    def test_lc_prefix_allowed(self):
        model = ShellExecutionModel()
        env = model.build_environment({"LC_MESSAGES": "en_US.UTF-8"})
        assert env.get("LC_MESSAGES") == "en_US.UTF-8"

    def test_none_value_skipped(self):
        model = ShellExecutionModel()
        env = model.build_environment({"FORCE_COLOR": None})
        assert "FORCE_COLOR" not in env

    def test_non_dict_extra_env_ignored(self):
        model = ShellExecutionModel()
        env = model.build_environment("not a dict")
        assert isinstance(env, dict)


# ── _enforce_workspace_boundary ───────────────────────────


class TestEnforceWorkspaceBoundary:
    def test_no_workspace_root_skips_check(self):
        model = ShellExecutionModel()
        model._enforce_workspace_boundary(["git", "/outside/path"], None)

    def test_absolute_path_inside_workspace_ok(self, tmp_path):
        model = ShellExecutionModel(workspace_root=tmp_path)
        inner = str(tmp_path / "file.txt")
        model._enforce_workspace_boundary(["cat", inner], None)

    def test_absolute_path_outside_workspace_raises(self, tmp_path):
        model = ShellExecutionModel(workspace_root=tmp_path / "project")
        outside = str(tmp_path / "other" / "secret.txt")
        with pytest.raises(ValueError, match="path_outside_workspace"):
            model._enforce_workspace_boundary(["cat", outside], None)

    def test_relative_path_ignored(self, tmp_path):
        model = ShellExecutionModel(workspace_root=tmp_path)
        model._enforce_workspace_boundary(["cat", "relative/file.txt"], None)


# ── execute (integration) ─────────────────────────────────


class TestExecute:
    @pytest.mark.skipif(os.name != "nt", reason="Windows echo test")
    def test_echo_success_on_windows(self, tmp_path):
        model = ShellExecutionModel(workspace_root=tmp_path)
        result = model.execute(command="echo hello")
        assert result["success"] is True
        assert "hello" in result["stdout"]

    @pytest.mark.skipif(os.name == "nt", reason="Unix echo test")
    def test_echo_success_on_unix(self, tmp_path):
        model = ShellExecutionModel(workspace_root=tmp_path)
        result = model.execute(command="echo hello")
        assert result["success"] is True
        assert "hello" in result["stdout"]
