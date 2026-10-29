"""headless 日志目录隔离（风险台账 #180，2026-09-18）。

原实现 ``_setup_logging`` 里硬编码 ``<repo>/思忆集app/data/logs``，
无视 ``SIYI_DATA_DIR`` → 隔离实例（集成测试 / AUTOSTART 临时后端 / 并行
pytest）的日志仍 append 进**真实** ``data/logs/siyi.log``：

* 污染用户数据目录（与"生产数据泄漏守卫"同类问题）；
* 多实例并发时 RotatingFileHandler rollover 撞 WinError 32。

本文件钉两条：
1. ``_resolve_log_dir()`` 尊重 ``SIYI_DATA_DIR``（未设时回落默认，行为不变）；
2. 文件日志初始化失败时，**全局异常钩子不得因 NameError 静默消失**
   （``log_dir`` 必须在 try 之前绑定）。
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture()
def headless_main(monkeypatch: pytest.MonkeyPatch):
    """导入 backend.headless_main（不触发 main()），用完剔除避免污染别的用例。"""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    mod = importlib.import_module("backend.headless_main")
    yield mod
    sys.path.remove(str(Path(__file__).resolve().parents[1]))
    del sys.modules["backend.headless_main"]


def test_log_dir_honors_siyi_data_dir(headless_main, tmp_path: Path, monkeypatch) -> None:
    """设了 SIYI_DATA_DIR → 日志目录必须落在该目录下（不再是真实源码树）。"""
    monkeypatch.setenv("SIYI_DATA_DIR", str(tmp_path))
    resolved = Path(headless_main._resolve_log_dir()).resolve()
    assert resolved == (tmp_path / "logs").resolve()
    # 关键：不得指回仓库真实 data/logs
    assert "思忆集app" not in str(resolved)


def test_log_dir_falls_back_when_env_unset(headless_main, monkeypatch) -> None:
    """未设 SIYI_DATA_DIR → 回落 <repo>/思忆集app/data/logs（向后兼容，行为不变）。"""
    monkeypatch.delenv("SIYI_DATA_DIR", raising=False)
    resolved = Path(headless_main._resolve_log_dir()).resolve()
    assert resolved.name == "logs"
    assert resolved.parent.name == "data"


def test_blank_env_is_treated_as_unset(headless_main, monkeypatch) -> None:
    """空串/空白视为未设（与 fallback.py:161 的 .strip() 语义一致）。"""
    monkeypatch.setenv("SIYI_DATA_DIR", "   ")
    resolved = Path(headless_main._resolve_log_dir()).resolve()
    assert resolved.name == "logs"
    assert resolved.parent.name == "data"


def test_excepthook_still_attempted_when_file_logging_fails(headless_main, monkeypatch) -> None:
    """★ 文件日志失败时，异常钩子仍被尝试安装（log_dir 不得未绑定）。

    历史缺陷：``log_dir`` 在 try 内赋值，RotatingFileHandler 一失败就是
    NameError，被宽 except 吞成一句 warning → **全局崩溃钩子静默消失**。
    这里让 makedirs 抛错，断言钩子仍收到**已解析好的** log_dir。
    """
    import logging

    calls: list[str] = []

    def _fake_install(*, log_dir: str, app_name: str) -> None:
        calls.append(log_dir)

    monkeypatch.setattr(headless_main, "_resolve_log_dir", lambda: str(Path("D:/definitely/not/creatable/logs")))
    monkeypatch.setattr(os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr("core.exception_handler.install_global_excepthook", _fake_install, raising=False)
    monkeypatch.setattr("core.exception_handler.install_log_sanitizer", lambda: None, raising=False)

    root = logging.getLogger()
    before = list(root.handlers)
    try:
        headless_main._setup_logging()
    finally:
        # 复原 root logger，避免污染后续用例
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)

    assert calls, "文件日志失败时异常钩子被跳过（log_dir 未绑定 → NameError 被吞）"
    assert calls[0] == str(Path("D:/definitely/not/creatable/logs"))
