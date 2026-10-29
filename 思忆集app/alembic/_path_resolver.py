"""Alembic 迁移目标数据库路径解析（P2-1）。

抽成独立模块是为了让测试可以直接 import 验证路径优先级，而不需要加载
``alembic/env.py`` 顶部那套 ``from alembic import context`` 上下文机
制（在非 CLI 调用下 ``context.config`` / ``context.is_offline_mode()``
都会抛 NameError）。

路径解析优先级：
1. ``SIYI_DATA_DIR`` 环境变量 + ``local_storage.db``
2. ``LocalDataStore`` 默认路径（``思忆集app/data/data/local_storage.db``）

与 ``data/local_storage.py:LocalDataStore.__init__`` 的默认 ``base_dir``
保持一致——迁移必须落在运行时实际使用的同一个 db 文件上，否则会出现
「迁移跑了但应用没看到」的割裂。
"""
from __future__ import annotations

import os
from pathlib import Path

# alembic/ 的上一级即 思忆集app/
APP_ROOT = Path(__file__).resolve().parents[1]

# 与 data/sqlite_store.py 使用的文件名保持一致
DB_FILENAME = "local_storage.db"


def resolve_db_path() -> str:
    """返回 Alembic 迁移应操作的 SQLite 文件绝对路径。

    优先级：
    1. ``SIYI_DATA_DIR`` 环境变量（非空时）+ ``local_storage.db``
    2. ``LocalDataStore`` 默认路径：
       ``思忆集app/data/data/local_storage.db``
       （对应 ``LocalDataStore.__init__`` 默认 ``base_dir``）
    """
    env_data_dir = os.environ.get("SIYI_DATA_DIR", "").strip()
    if env_data_dir:
        return os.path.join(env_data_dir, DB_FILENAME)

    # 默认路径：思忆集app/data/data/
    # 注意这里是两层 data —— 外层是 data 包目录，
    # 内层是 LocalDataStore 默认 base_dir，
    # 二者拼接后才是运行时真正落盘的位置，
    # 与 LocalDataStore.__init__ 的默认值严格对齐。
    default_data_dir = os.path.join(str(APP_ROOT), "data", "data")
    return os.path.join(default_data_dir, DB_FILENAME)
