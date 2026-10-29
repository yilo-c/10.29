"""Siyi Collection Alembic migration environment (P2-1).

Differences from standard Alembic template:
1. Database path resolved from LocalDataStore (consistent with runtime)
2. Uses SQLAlchemy create_engine for migration execution (Alembic requirement)
   but the actual application runtime still uses raw sqlite3

Database path resolution priority:
1. ``SIYI_DATA_DIR`` env var + ``local_storage.db``
2. ``LocalDataStore`` default path (``思忆集app/data/data/local_storage.db``)
3. ``alembic.ini`` ``sqlalchemy.url`` placeholder
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 让 alembic/ 下的脚本能 import 到 思忆集app 包内模块
THIS_DIR = Path(__file__).resolve().parent
APP_ROOT = THIS_DIR.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# 以文件路径方式加载 _path_resolver，避免依赖 alembic/ 是否为包：
# Alembic CLI 会把 env.py 当脚本执行，此时相对 import 不可用，
# 而绝对 import 又会和顶层同名模块冲突。
import importlib.util

_path_resolver_path = THIS_DIR / "_path_resolver.py"
_spec = importlib.util.spec_from_file_location(
    "siyi_alembic_path_resolver", _path_resolver_path
)
_path_resolver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_path_resolver)
_resolve_db_path = _path_resolver.resolve_db_path

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Offline mode: generate SQL scripts without connecting to DB."""
    db_path = _resolve_db_path()
    url = f"sqlite:///{db_path}"
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online mode: connect to DB via SQLAlchemy engine and execute migrations.

    Uses create_engine (Alembic requirement) with SQLite URL.
    The application runtime uses raw sqlite3; this engine is migration-only.
    """
    db_path = _resolve_db_path()
    db_path_full = db_path if os.path.isabs(db_path) else os.path.join(str(APP_ROOT), db_path)

    # 目标目录可能尚未创建（首次运行）
    parent = os.path.dirname(db_path_full)
    if parent:
        os.makedirs(parent, exist_ok=True)

    # 覆盖 alembic.ini 里的占位 url，始终指向运行时实际使用的 db
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path_full}")

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
