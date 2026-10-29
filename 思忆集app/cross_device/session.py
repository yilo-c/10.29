"""跨端会话续接：桌面 ↔ 手机携带对话上下文。

根据 dual-positioning-architecture-plan.md 第 5.1 节设计：
- 设备端保存会话状态到 automation-hub
- 上下文以向量摘要 + 未完成 action 的形式存储
- 新设备检测到切换事件后主动提示续接
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.cross_device.session")

# 设备类型常量
DEVICE_DESKTOP = "desktop"
DEVICE_ANDROID = "android"

# 会话状态
SESSION_ACTIVE = "active"
SESSION_PAUSED = "paused"
SESSION_TRANSFERRED = "transferred"
SESSION_CLOSED = "closed"


@dataclass
class SessionContext:
    """会话上下文快照。"""
    session_id: str
    device_type: str                              # 来源设备类型
    topic: str = ""                               # 当前讨论主题摘要
    last_message: str = ""                        # 最后一条消息
    vector_summary: str = ""                      # 向量摘要 / 语义指纹
    pending_actions: list[dict[str, Any]] = field(default_factory=list)  # 待完成操作
    message_count: int = 0
    started_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "device_type": self.device_type,
            "topic": self.topic,
            "last_message": self.last_message,
            "vector_summary": self.vector_summary,
            "pending_actions": list(self.pending_actions),
            "message_count": self.message_count,
            "started_at": self.started_at,
            "updated_at": self.updated_at or datetime.now(timezone.utc).isoformat(),
            "metadata": dict(self.metadata),
        }


class CrossDeviceSessionManager:
    """跨端会话续接管理器。

    职责：
    1. 保存当前设备会话状态
    2. 检测设备切换事件
    3. 在新设备上主动提示续接
    4. 恢复完整上下文到目标设备
    """

    def __init__(self, data_store=None, sync_orchestrator=None):
        self._data_store = data_store
        self._sync_orchestrator = sync_orchestrator
        self._active_sessions: dict[str, SessionContext] = {}
        self._last_active_device: str | None = None

    def save_session(
        self,
        *,
        session_id: str,
        device_type: str,
        topic: str = "",
        last_message: str = "",
        vector_summary: str = "",
        pending_actions: list[dict[str, Any]] | None = None,
        message_count: int = 0,
    ) -> SessionContext:
        """保存当前设备会话状态。

        内部自动检测设备切换——如果当前 device_type 与
        上一次保存时的设备不同，会在返回的 SessionContext
        的 metadata 中设置 switch_from 字段。
        """
        now = datetime.now(timezone.utc).isoformat()

        # 检测设备切换
        switch_info = self.detect_device_switch(device_type)

        started_at = now
        if session_id in self._active_sessions:
            started_at = self._active_sessions[session_id].started_at

        ctx = SessionContext(
            session_id=session_id,
            device_type=device_type,
            topic=topic,
            last_message=last_message[:500],
            vector_summary=vector_summary,
            pending_actions=list(pending_actions or []),
            message_count=message_count,
            started_at=started_at,
            updated_at=now,
        )

        if switch_info:
            ctx.metadata["switch_from"] = switch_info["from_device"]
            logger.info(
                "检测到设备切换: %s → %s (会话 %s)",
                switch_info["from_device"], device_type, session_id,
            )

        self._active_sessions[session_id] = ctx
        self._last_active_device = device_type
        self._sync_to_hub(session_id, ctx)
        return ctx

    def detect_device_switch(self, device_type: str) -> dict[str, Any] | None:
        """检测是否发生了设备切换。

        返回切换提示信息（含续接上下文）或 None。
        """
        if self._last_active_device is None:
            self._last_active_device = device_type
            return None

        if device_type == self._last_active_device:
            return None

        # 找到最后活跃的会话
        last_session = self._get_latest_session()
        if last_session is None:
            self._last_active_device = device_type
            return None

        self._last_active_device = device_type
        return {
            "switch_detected": True,
            "from_device": last_session.device_type,
            "to_device": device_type,
            "session_id": last_session.session_id,
            "topic": last_session.topic,
            "last_message": last_session.last_message,
            "pending_actions_count": len(last_session.pending_actions),
            "message_count": last_session.message_count,
            "prompt": f"你在 {last_session.device_type} 上正在讨论「{last_session.topic}」，要继续吗？",
        }

    def restore_session(self, session_id: str) -> SessionContext | None:
        """在目标设备上恢复指定会话的完整上下文。"""
        ctx = self._active_sessions.get(session_id)
        if ctx is None:
            ctx = self._load_from_hub(session_id)
        if ctx is None:
            return None

        # 标记原会话为已转移
        ctx = SessionContext(
            session_id=ctx.session_id,
            device_type=ctx.device_type,
            topic=ctx.topic,
            last_message=ctx.last_message,
            vector_summary=ctx.vector_summary,
            pending_actions=list(ctx.pending_actions),
            message_count=ctx.message_count,
            started_at=ctx.started_at,
            updated_at=datetime.now(timezone.utc).isoformat(),
            metadata={**ctx.metadata, "restored": True},
        )
        self._active_sessions[session_id] = ctx
        return ctx

    def add_pending_action(self, session_id: str, action: dict[str, Any]) -> None:
        """向会话添加待完成操作（如待审批的操作）。"""
        ctx = self._active_sessions.get(session_id)
        if ctx is None:
            return
        ctx.pending_actions.append(action)
        ctx.updated_at = datetime.now(timezone.utc).isoformat()
        self._sync_to_hub(session_id, ctx)

    def close_session(self, session_id: str) -> None:
        if session_id in self._active_sessions:
            self._active_sessions[session_id] = SessionContext(
                session_id=session_id,
                device_type=self._active_sessions[session_id].device_type,
                topic=self._active_sessions[session_id].topic,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            del self._active_sessions[session_id]

    def shutdown(self) -> None:
        """关闭所有活跃会话并清理状态。"""
        for sid in list(self._active_sessions.keys()):
            try:
                self.close_session(sid)
            except Exception:
                logger.warning("failed to close session %s on shutdown", sid, exc_info=True)
        self._active_sessions.clear()
        self._last_active_device = None
        logger.debug("CrossDeviceSessionManager shut down, %d sessions closed", len(self._active_sessions))

    def _get_latest_session(self) -> SessionContext | None:
        """获取最后更新的活跃会话。"""
        if not self._active_sessions:
            return None
        return max(self._active_sessions.values(), key=lambda s: s.updated_at or "")

    def _sync_to_hub(self, session_id: str, ctx: SessionContext) -> None:
        # 波次④包A（§2-② 保守修法）：session_context 既不在 A 栈 RESOURCE_SPECS，
        # 也不在 Worker ALLOWED_RESOURCES。旧代码调 orchestrator.sync_resource(
        # resource_type=...) 与被调方签名 (resource_name, limit) 不匹配必 TypeError，
        # 又被 except 静默吞掉——所谓"上云"实为幻影，永远回退本地。现删除该幻影分支，
        # 显式声明本地 only，消除假成功。
        logger.debug(
            "cross_device_session_local_only: 会话 %s 持久化到本地（未上云）", session_id
        )
        if self._data_store is not None:
            try:
                sessions = self._data_store.load_json("cross_device_sessions", {})
                sessions[session_id] = ctx.to_dict()
                self._data_store.save_json("cross_device_sessions", sessions)
            except Exception:
                logger.warning("本地持久化会话失败", exc_info=True)

    def _load_from_hub(self, session_id: str) -> SessionContext | None:
        # 同上：显式本地 only，不再走 orchestrator.fetch_resource 幻影调用（该方法不存在）。
        if self._data_store is not None:
            try:
                sessions = self._data_store.load_json("cross_device_sessions", {})
                data = sessions.get(session_id)
                if data and isinstance(data, dict):
                    return self._dict_to_session(session_id, data)
            except Exception:
                logger.warning("从本地加载会话失败", exc_info=True)
        return None

    @staticmethod
    def _dict_to_session(session_id: str, data: dict[str, Any]) -> SessionContext:
        return SessionContext(
            session_id=data.get("session_id", session_id),
            device_type=data.get("device_type", DEVICE_DESKTOP),
            topic=data.get("topic", ""),
            last_message=data.get("last_message", ""),
            vector_summary=data.get("vector_summary", ""),
            pending_actions=list(data.get("pending_actions", [])),
            message_count=int(data.get("message_count", 0)),
            started_at=data.get("started_at", ""),
            updated_at=data.get("updated_at", ""),
        )

    def get_status_snapshot(self) -> dict[str, Any]:
        return {
            "active_sessions": len(self._active_sessions),
            "last_active_device": self._last_active_device,
            "latest_session_topic": (
                self._get_latest_session().topic if self._get_latest_session() else None
            ),
        }
