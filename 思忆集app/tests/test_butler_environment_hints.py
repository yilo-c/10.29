"""回归测试：管家环境提示注入（2026-09-11）。

背景（生产 `trace.db` 实证）：模型把桌面猜成 ``C:\\Users\\PC-20230405JYXU\\Desktop``
——该用户名并不存在——导致 `file_read` 反复以 `path_outside_allowed_root` 失败。

读桌面其实**早已被允许**：`assistant/capability/_file_ops.py` 的 `_read_roots()`
明确把 `Path.home() / "Desktop"` 列入（注释：「添加桌面目录，让 AI 可以读取
用户桌面文件」）。真正的缺口是模型不知道本机真实路径。

本测试确保环境提示里包含真实路径与「禁止猜测」约束，防止该幻觉回归。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from assistant.agents.butler_agent import _environment_hints


def test_environment_hints_contain_real_home():
    """必须给出真实用户主目录，供模型直接使用。"""
    hints = _environment_hints(SimpleNamespace(local_data_store=None))
    assert str(Path.home()) in hints


def test_environment_hints_contain_desktop_when_present():
    """桌面存在时须给出其真实绝对路径。"""
    desktop = Path.home() / "Desktop"
    hints = _environment_hints(SimpleNamespace(local_data_store=None))
    if desktop.exists():
        assert str(desktop) in hints


def test_environment_hints_forbid_guessing_paths():
    """必须显式禁止凭猜测拼造路径（本次幻觉的直接对策）。"""
    hints = _environment_hints(SimpleNamespace(local_data_store=None))
    assert "不要凭猜测" in hints


def test_environment_hints_include_data_dir_when_available(tmp_path):
    """有 local_data_store.base_dir 时应列出可读写的数据目录。"""
    ctx = SimpleNamespace(local_data_store=SimpleNamespace(base_dir=str(tmp_path)))
    hints = _environment_hints(ctx)
    assert str(tmp_path) in hints


def test_environment_hints_tolerate_missing_data_store():
    """local_data_store 缺失时不得抛异常（降级安全）。"""
    hints = _environment_hints(SimpleNamespace())
    assert str(Path.home()) in hints
