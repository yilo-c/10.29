"""使用 Alembic 初始化/升级数据库 schema。

替代旧的 schema.sql 方式。所有表定义由 ``api.models`` 中的
SQLAlchemy 2.0 ORM 模型统一管理。
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config
from alembic import command


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CFG_PATH = PROJECT_ROOT / "alembic.ini"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "automation_hub.sqlite3")))


def main() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    alembic_cfg = Config(str(ALEMBIC_CFG_PATH))
    alembic_cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))

    # 让 Alembic 使用正确的数据库路径
    os.environ["DATABASE_PATH"] = str(DATABASE_PATH)

    command.upgrade(alembic_cfg, "head")

    print(f"Database migrated (alembic head): {DATABASE_PATH}")


if __name__ == "__main__":
    main()