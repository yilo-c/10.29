"""initial schema with kv_store table

Revision ID: 0001
Revises:
Create Date: 2026-08-02 00:00:00

P2-1: 首个 Alembic 迁移，建立 schema 版本管理框架。

收录的表：
1. ``alembic_version`` — Alembic 自带版本表（由 Alembic 自动创建管理）
2. ``kv_store`` — 主 KV 存储（与 data/sqlite_store.py:_SCHEMA_SQL 完全一致）

注意：
- 此迁移用 ``CREATE TABLE IF NOT EXISTS`` 幂等执行，对已有数据库无副作用
- ``kv_store`` 表的 DDL 必须与 SqliteKVStore._SCHEMA_SQL 保持一致
- 未来新增表时，创建新迁移脚本而非修改此文件
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 与 data/sqlite_store.py:_SCHEMA_SQL 保持逐字一致，
    # IF NOT EXISTS 保证对已存在的库是无副作用的幂等操作。
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS kv_store (
            key            TEXT PRIMARY KEY,
            value          TEXT NOT NULL,
            updated_at     REAL NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    # 历史版本遗留的索引，已不再使用，顺带清理（不存在时静默跳过）
    op.execute("DROP INDEX IF EXISTS idx_kv_store_updated_at")


def downgrade() -> None:
    # 回滚即丢弃整张 KV 表——仅用于开发期重置，
    # 生产环境请先备份 local_storage.db。
    op.execute("DROP TABLE IF EXISTS kv_store")
