"""Inbox, web, knowledge, and info JSON-RPC handlers（薄适配层）。

原实现已按 4 个域拆分为子模块：
  - ``_inbox_crud.py``     → inbox.*（收件箱 CRUD + AI 分类 + 人工确认）
  - ``_inbox_web.py``       → web.*（联网检索）
  - ``_inbox_knowledge.py`` → knowledge.*（知识图谱 + 归纳 + MD 读写）
  - ``_inbox_info.py``      → info.*（资讯聚合）

本文件仅保留 ``register_inbox_data`` 作为统一装配入口，把注册职责下推到各子模块，
不再保留任何实现逻辑。所有现有调用方（method_registry 装配、测试里
``from backend.handlers._inbox_data import register_inbox_data``）行为完全不受影响。
"""

from __future__ import annotations

from backend.handlers._inbox_crud import register_inbox_crud
from backend.handlers._inbox_web import register_inbox_web
from backend.handlers._inbox_knowledge import register_inbox_knowledge
from backend.handlers._inbox_info import register_inbox_info


def register_inbox_data(registry, context) -> None:
    """Register inbox, web, knowledge, and info-related JSON-RPC methods."""
    register_inbox_crud(registry, context)
    register_inbox_web(registry, context)
    register_inbox_knowledge(registry, context)
    register_inbox_info(registry, context)
