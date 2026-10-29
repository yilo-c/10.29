"""领域子门面（P1-4：AppContext God Object 渐进拆分第一步）。

AppContext 仍是装配根；handlers/服务**新代码优先经领域门面取依赖**，
降低对"全量上下文"的直接耦合。门面是薄视图——只代理 AppContext 上
已存在的属性，不引入任何新生命周期；属性缺失时返回 None（与
handler 里 ``getattr(context, "x", None)`` 的既有容错语义一致）。

迁移示范见 backend/handlers/_trace.py；其余 handler 逐步跟进。
"""

from __future__ import annotations

from typing import Any


class _DomainFacade:
    """门面基类：按 _attrs 白名单代理宿主上下文属性。"""

    _attrs: tuple[str, ...] = ()

    def __init__(self, ctx: Any) -> None:
        object.__setattr__(self, "_ctx", ctx)

    def __getattr__(self, name: str) -> Any:
        if name in type(self)._attrs:
            return getattr(object.__getattribute__(self, "_ctx"), name, None)
        raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<{type(self).__name__} attrs={list(type(self)._attrs)}>"


class ChatFacade(_DomainFacade):
    """聊天/对话域：AI 客户端、存储、观测、笔记/任务仓储。"""

    # R0-B19：属性名必须与 _context_builder 注入集一致（personal_*）；
    # 曾误列 note_repository/task_repository（AppContext 不存在）导致门面恒返 None，
    # 由 scripts/audit_context_attr_wiring.py 同型扫描发现。
    _attrs = (
        "ai_client",
        "settings_repository",
        "local_data_store",
        "trace_recorder",
        "personal_note_repository",
        "personal_task_repository",
    )


class MemoryFacade(_DomainFacade):
    """记忆/画像域：记忆索引、用户画像、收件箱服务。"""

    _attrs = (
        "memory_index_service",
        "user_profile_service",
        "inbox_service",
        "knowledge_base_bridge",
    )


class SyncFacade(_DomainFacade):
    """同步域：云同步桥与同步编排。"""

    _attrs = (
        "cloud_sync_bridge",
        "sync_orchestrator",
    )


class AutomationFacade(_DomainFacade):
    """自动化域：定时任务、持久队列、自治管理器、进化编排。"""

    _attrs = (
        "cron_service",
        "task_queue",
        "autonomous_assistant_manager",
        "evolution_orchestrator",
        "agent_directory",
    )


__all__ = [
    "ChatFacade",
    "MemoryFacade",
    "SyncFacade",
    "AutomationFacade",
]
