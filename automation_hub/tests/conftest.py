# conftest.py — project-local temp directory + sys.path for api.* imports
from __future__ import annotations

import sys
from pathlib import Path


def pytest_configure(config: object) -> None:
    # add automation-hub/ to sys.path so api.* and bare agent/worker imports work
    hub_dir = Path(__file__).resolve().parent.parent
    if str(hub_dir) not in sys.path:
        sys.path.insert(0, str(hub_dir))

    # 不固定 basetemp：让 pytest 使用默认的随机临时目录（位于系统 temp 下，
    # 自动随进程退出清理，不污染 git 仓库）。
    # 历史固定路径 ``siyi_hub_pytest_tmp`` 在 Windows 沙箱回收站不可用时无法被
    # safe-delete 清理而残留，导致后续 session 清理期 mkdir 报 FileExistsError
    # 使大量 fixture setup 失败。默认行为无此问题。
