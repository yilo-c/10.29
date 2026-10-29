"""探测 desk-notes Electron 数据目录。

与 desk-notes/packages/desktop/electron/main.cjs 的 resolveUserDataDir 保持同序：
  1. 环境变量 SIYI_DESKNOTES_DATA_DIR（思忆集后端专用覆盖，**权威**：见下）
  2. 环境变量 ELECTRON_USER_DATA_DIR/siyi-notes（与 Electron 主进程一致）
  3. %LOCALAPPDATA%\\desk-notes\\siyi-notes  （Windows 默认）
  4. <repo>/desk-notes/packages/desktop/data/siyi-notes  （开发回退）

存在 notes/ 子目录视为有效；找不到返回 None（调用方回退到本地 JSON 存储）。

P0-2（env-drill T1 首冒烟实锤，2026-09-17）：``SIYI_DESKNOTES_DATA_DIR`` 是**显式
隔离意图**，一旦设置即权威——目标目录缺 ``notes/`` 时**直接 raise**，绝不继续遍历
候选链。旧行为下循环会 ``continue`` 落到 ``%LOCALAPPDATA%`` 生产目录，把隔离调用方
（演练 harness / 测试 / 用户脚本）的写入静默灌进真实用户笔记。

判据本体已收敛到 :func:`core.isolation.resolve_isolated_dir`（P0-2 治本，
2026-09-18）——同一语义供所有 ``*_DATA_DIR`` 类 override 复用，不在本模块自写。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from core.isolation import resolve_isolated_dir

logger = logging.getLogger("siyi.desknotes_path")

_SUFFIX = "siyi-notes"


def _candidate_dirs() -> list[Path]:
    """fallback 候选链（**不含** ``SIYI_DESKNOTES_DATA_DIR``）。

    该 override 由 :func:`resolve_desknotes_dir` 权威处理，绝不参与本链——
    否则显式隔离失败时会静默回退到生产目录（P0-2）。
    """
    candidates: list[Path] = []

    electron_override = os.environ.get("ELECTRON_USER_DATA_DIR")
    if electron_override:
        candidates.append(Path(electron_override) / _SUFFIX)

    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            candidates.append(Path(local_app) / "desk-notes" / _SUFFIX)
    else:
        # macOS / Linux 仅供测试，与 Electron 默认一致
        home = Path.home()
        candidates.append(home / "Library" / "Application Support" / "desk-notes" / _SUFFIX)
        candidates.append(home / ".config" / "desk-notes" / _SUFFIX)

    # 开发回退：仓库内 desk-notes/packages/desktop/data
    here = Path(__file__).resolve()
    for parent in here.parents:
        guess = parent / "desk-notes" / "packages" / "desktop" / "data" / _SUFFIX
        if guess.exists():
            candidates.append(guess)
            break

    return candidates


def resolve_desknotes_dir() -> str | None:
    """返回首个存在 notes/ 子目录的候选路径，否则 None。

    ``SIYI_DESKNOTES_DATA_DIR`` 一旦显式设置即**权威**（P0-2）：目标目录缺 ``notes/``
    时直接 raise，绝不继续遍历候选链。判据本体在
    :func:`core.isolation.resolve_isolated_dir`——同一语义对所有 ``*_DATA_DIR`` 类
    override 复用，不再逐处自写一套回退逻辑。
    """
    if os.environ.get("SIYI_DISABLE_DESKNOTES_BRIDGE"):
        logger.info("desknotes bridge disabled via SIYI_DISABLE_DESKNOTES_BRIDGE")
        return None

    override = resolve_isolated_dir(
        "SIYI_DESKNOTES_DATA_DIR",
        required_subdir="notes",
        hint_disable_key="SIYI_DISABLE_DESKNOTES_BRIDGE",
    )
    if override is not None:
        return str(override)

    for candidate in _candidate_dirs():
        try:
            notes_dir = candidate / "notes"
            if notes_dir.is_dir():
                logger.info("desknotes data dir resolved: %s", candidate)
                return str(candidate)
        except OSError:
            continue

    logger.info("desknotes data dir not found (falling back to local JSON store)")
    return None
