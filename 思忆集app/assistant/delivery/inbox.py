"""统一结果交付通道：收件箱 + 桌面通知 + 聊天 + 悬浮 + 字幕。

所有主动/被动结果都必须通过此模块交付，不允许插件或能力层直接弹窗。

交付通道：
- inbox:           桌面内收件箱（默认，低优先级结果）
- notification:    桌面系统通知（高优提醒：定时、异常、强关注）
- both:            同时进收件箱 + 桌面通知
- chat_output:     聊天面板输出（对话式回复）
- floating_output: 悬浮助手气泡（快捷操作结果）
- subtitle_output: 底部字幕条（短时提示，自动消失）
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from core.events import AppEvent, event_bus
from domain.inbox_models import InboxEntry

logger = logging.getLogger("siyi.assistant.inbox")

# ── 三层流转状态 ─────────────────────────────────────────
STAGE_INBOX = "inbox"
STAGE_DAILY = "daily"
STAGE_INSIGHTS = "insights"
ALL_STAGES = frozenset({STAGE_INBOX, STAGE_DAILY, STAGE_INSIGHTS})

# ── 交付通道常量 ─────────────────────────────────────────
CHANNEL_INBOX = "inbox"
CHANNEL_NOTIFICATION = "notification"
CHANNEL_BOTH = "both"
CHANNEL_CHAT = "chat_output"
CHANNEL_FLOATING = "floating_output"
CHANNEL_SUBTITLE = "subtitle_output"

ALL_CHANNELS = frozenset({
    CHANNEL_INBOX, CHANNEL_NOTIFICATION, CHANNEL_BOTH,
    CHANNEL_CHAT, CHANNEL_FLOATING, CHANNEL_SUBTITLE,
})

# ── 优先级 ───────────────────────────────────────────────
PRIORITY_LOW = "low"
PRIORITY_NORMAL = "normal"
PRIORITY_HIGH = "high"
PRIORITY_URGENT = "urgent"

# ── 来源类别 ─────────────────────────────────────────────
SOURCE_SCHEDULED = "scheduled"
SOURCE_ANOMALY = "anomaly"
SOURCE_TASK_RESULT = "task_result"
SOURCE_PROACTIVE = "proactive"
SOURCE_PLUGIN = "plugin"
SOURCE_MANUAL = "manual"

_STORAGE_KEY = "assistant_inbox_items"


class InboxItem:
    """收件箱条目。"""

    __slots__ = (
        "channel",
        "content",
        "created_at",
        "daily_at",
        "dismissed_at",
        "insights_at",
        "item_id",
        "layer",
        "metadata",
        "priority",
        "read_at",
        "source",
        "source_id",
        "status",
        "tags",
        "title",
        "updated_at",
    )

    def __init__(
        self,
        *,
        title: str,
        content: str = "",
        channel: str = CHANNEL_INBOX,
        priority: str = PRIORITY_NORMAL,
        source: str = SOURCE_MANUAL,
        source_id: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        layer: str = STAGE_INBOX,
    ):
        self.item_id = uuid.uuid4().hex[:12]
        self.title = title
        self.content = content
        self.channel = channel
        self.priority = priority
        self.source = source
        self.source_id = source_id
        self.tags = tags or []
        self.layer = layer if layer in ALL_STAGES else STAGE_INBOX
        self.status = "unread"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at
        self.read_at = ""
        self.daily_at = ""
        self.insights_at = ""
        self.dismissed_at = ""
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "content": self.content,
            "channel": self.channel,
            "priority": self.priority,
            "source": self.source,
            "source_id": self.source_id,
            "tags": self.tags,
            "layer": self.layer,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "read_at": self.read_at,
            "daily_at": self.daily_at,
            "insights_at": self.insights_at,
            "dismissed_at": self.dismissed_at,
            "metadata": self.metadata,
        }


class AssistantInboxService:
    """统一交付通道服务。"""

    def __init__(
        self,
        *,
        data_store,
        notification_callback: Callable[[dict[str, Any]], None] | None = None,
        chat_callback: Callable[[dict[str, Any]], None] | None = None,
        floating_callback: Callable[[dict[str, Any]], None] | None = None,
        subtitle_callback: Callable[[dict[str, Any]], None] | None = None,
        user_profile_service=None,
        max_items: int = 500,
    ):
        self.data_store = data_store
        self._notification_callback = notification_callback
        self._chat_callback = chat_callback
        self._floating_callback = floating_callback
        self._subtitle_callback = subtitle_callback
        self._user_profile_service = user_profile_service
        self._max_items = max_items
        self._event_bus = event_bus

    # ── 核心交付 ─────────────────────────────────────────

    def deliver(
        self,
        *,
        title: str,
        content: str = "",
        channel: str | None = None,
        priority: str = PRIORITY_NORMAL,
        source: str = SOURCE_MANUAL,
        source_id: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """统一交付入口。所有结果（插件、定时、任务、主动推送）都走这里。"""
        # 自动决定通道
        resolved_channel = channel or self._resolve_channel(priority, source)

        # 安静时段降级：弹出式通道降级为静默收件箱
        if self._user_profile_service is not None and self._user_profile_service.is_in_quiet_hours():
            if resolved_channel in (CHANNEL_NOTIFICATION, CHANNEL_FLOATING, CHANNEL_SUBTITLE):
                resolved_channel = CHANNEL_INBOX
                logger.debug("安静时段，通知降级为收件箱: %s", title)
            elif resolved_channel == CHANNEL_BOTH:
                resolved_channel = CHANNEL_INBOX

        item = InboxItem(
            title=title,
            content=content,
            channel=resolved_channel,
            priority=priority,
            source=source,
            source_id=source_id,
            tags=tags,
            metadata=metadata,
        )

        # 写入收件箱
        if resolved_channel in (CHANNEL_INBOX, CHANNEL_BOTH):
            self._persist_item(item)

        # 发送桌面通知
        if resolved_channel in (CHANNEL_NOTIFICATION, CHANNEL_BOTH):
            self._send_notification(item)

        # 聊天面板输出
        if resolved_channel == CHANNEL_CHAT:
            self._send_to_channel(self._chat_callback, item, "chat")
            self._persist_item(item)

        # 悬浮助手气泡
        if resolved_channel == CHANNEL_FLOATING:
            self._send_to_channel(self._floating_callback, item, "floating")
            self._persist_item(item)

        # 字幕条
        if resolved_channel == CHANNEL_SUBTITLE:
            self._send_to_channel(self._subtitle_callback, item, "subtitle")

        # 触发 EventBus 事件（best-effort）
        try:
            if self._event_bus is not None:
                self._event_bus.emit(AppEvent(
                    event_type="inbox.delivered",
                    source="assistant_inbox",
                    payload={
                        "item_id": item.item_id,
                        "title": item.title,
                        "channel": item.channel,
                        "priority": item.priority,
                        "source": item.source,
                        # P2.3: 透传 metadata 与 tags，让前端能识别 trigger 类型等上下文
                        "metadata": item.metadata or {},
                        "tags": list(item.tags) if item.tags else [],
                    },
                ))
        except Exception:
            logger.debug("EventBus emit failed for inbox.delivered", exc_info=True)

        return item.to_dict()

    def deliver_task_result(
        self,
        *,
        goal: str,
        result: dict[str, Any],
        success: bool = True,
        action_id: str = "",
        channel: str | None = None,
    ) -> dict[str, Any]:
        """交付任务执行结果。

        channel 参数允许调用方指定交付通道（如 subtitle_output），
        如果不传则走自动通道解析（based on priority/source）。
        """
        status_emoji = "✅" if success else "❌"
        title = f"{status_emoji} 任务完成: {goal[:60]}"
        content = str(result.get("summary") or result.get("reason") or "")[:500]
        priority = PRIORITY_NORMAL if success else PRIORITY_HIGH
        return self.deliver(
            title=title,
            content=content,
            channel=channel,
            priority=priority,
            source=SOURCE_TASK_RESULT,
            source_id=action_id,
            tags=["task_result"],
            metadata={"goal": goal, "success": success, "result_keys": list(result.keys())[:10]},
        )

    def deliver_scheduled(
        self,
        *,
        rule_name: str,
        content: str,
        priority: str = PRIORITY_NORMAL,
    ) -> dict[str, Any]:
        """交付定时/规则触发的结果。"""
        return self.deliver(
            title=f"馃搮 {rule_name}",
            content=content,
            channel=CHANNEL_BOTH if priority in (PRIORITY_HIGH, PRIORITY_URGENT) else CHANNEL_INBOX,
            priority=priority,
            source=SOURCE_SCHEDULED,
            tags=["scheduled"],
        )

    def deliver_proactive(
        self,
        *,
        title: str,
        content: str,
        category: str = "high_value_match",
        priority: str = PRIORITY_NORMAL,
    ) -> dict[str, Any]:
        """交付主动推送结果。先检查权限。"""
        if self._user_profile_service is not None:
            if not self._user_profile_service.is_proactive_allowed():
                logger.debug("主动推送被用户禁用: %s", title)
                return {"delivered": False, "reason": "proactive_disabled"}
            allowed = self._user_profile_service.get_proactive_categories()
            if category not in allowed:
                logger.debug("主动推送类别不在允许列表: %s (allowed=%s)", category, allowed)
                return {"delivered": False, "reason": f"category_not_allowed: {category}"}

        return self.deliver(
            title=title,
            content=content,
            priority=priority,
            source=SOURCE_PROACTIVE,
            tags=["proactive", category],
            metadata={"proactive_category": category},
        )

    # ── 查询 ─────────────────────────────────────────────

    def list_items(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出收件箱条目。"""
        items = self._load_items()
        if status:
            items = [i for i in items if i.get("status") == status]
        if source:
            items = [i for i in items if i.get("source") == source]
        return items[-limit:]

    def mark_read(self, item_id: str) -> bool:
        """标记为已读。"""
        result = self._update_item(item_id, {"status": "read", "read_at": datetime.now(timezone.utc).isoformat()})
        if result:
            try:
                self._event_bus.emit(AppEvent(
                    event_type="inbox.item_read",
                    source="assistant_inbox",
                    payload={"item_id": item_id, "unread_count": self.unread_count()},
                ))
            except Exception:
                logger.debug("EventBus emit failed for inbox.item_read", exc_info=True)
        return result

    def dismiss(self, item_id: str) -> bool:
        """忽略/关闭条目。"""
        return self._update_item(item_id, {"status": "dismissed", "dismissed_at": datetime.now(timezone.utc).isoformat()})

    # ── 三层流转 ─────────────────────────────────────────

    def promote_to_daily(self, item_id: str) -> bool:
        """将收件箱条目提升到 daily 层。"""
        now = datetime.now(timezone.utc).isoformat()
        return self._update_item(item_id, {
            "layer": STAGE_DAILY,
            "daily_at": now,
            "status": "read",
            "updated_at": now,
        })

    def promote_to_insights(self, item_id: str) -> bool:
        """将 daily 条目沉淀为 insights。"""
        now = datetime.now(timezone.utc).isoformat()
        return self._update_item(item_id, {
            "layer": STAGE_INSIGHTS,
            "insights_at": now,
            "status": "read",
            "updated_at": now,
        })

    def get_items_by_layer(self, layer: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """按层列出条目。"""
        if layer not in ALL_STAGES:
            return []
        items = self._load_items()
        return [i for i in items if i.get("layer") == layer][-limit:]

    def promote_batch(self, item_ids: list[str], target_layer: str) -> int:
        """批量提升条目到目标层。"""
        if target_layer == STAGE_DAILY:
            return sum(1 for iid in item_ids if self.promote_to_daily(iid))
        elif target_layer == STAGE_INSIGHTS:
            return sum(1 for iid in item_ids if self.promote_to_insights(iid))
        return 0

    def unread_count(self) -> int:
        items = self._load_items()
        return sum(1 for i in items if i.get("status") == "unread")

    # ── P1-4: 人工确认异步响应 ─────────────────────────────

    _CONFIRMATION_RESPONSE_SOURCE = "confirmation_response"

    def deliver_confirmation_request(
        self,
        *,
        confirm_id: str,
        title: str,
        content: str,
        risk_level: str = "medium",
        detail: str = "",
        chain_id: str = "",
        node_id: str = "",
        timeout_seconds: int = 60,
    ) -> dict[str, Any]:
        """P1-4: 投递人工确认请求到收件箱。

        前端可订阅 ``inbox.delivered`` 事件实时显示确认弹窗，
        用户点击 approve/reject 后调用 ``respond_to_confirmation`` 写回响应。
        """
        return self.deliver(
            title=title,
            content=content,
            channel=CHANNEL_BOTH if risk_level == "high" else CHANNEL_INBOX,
            priority=PRIORITY_HIGH if risk_level == "high" else PRIORITY_NORMAL,
            source="confirmation_request",
            source_id=confirm_id,
            tags=["confirmation_request", f"risk_{risk_level}"],
            metadata={
                "confirm_id": confirm_id,
                "risk_level": risk_level,
                "chain_id": chain_id,
                "node_id": node_id,
                "detail": detail,
                "timeout_seconds": timeout_seconds,
                "awaiting_response": True,
            },
        )

    def respond_to_confirmation(
        self,
        *,
        confirm_id: str,
        action: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """P1-4: 用户对确认请求的响应。

        Parameters
        ----------
        confirm_id : str
            确认请求 ID（与 deliver_confirmation_request 的 confirm_id 一致）
        action : str
            ``approve`` 或 ``reject``
        reason : str
            用户给出的理由（可选）

        Returns
        -------
        dict
            ``{"recorded": bool, "confirm_id": str, "action": str}``
        """
        if action not in ("approve", "reject"):
            return {"recorded": False, "error": f"invalid action: {action}"}

        # 写入一条响应条目，供 _agent_chain_confirm_callback 轮询读取
        self.deliver(
            title=f"确认响应: {action}",
            content=reason or f"用户{action}了确认请求 {confirm_id}",
            channel=CHANNEL_INBOX,
            priority=PRIORITY_NORMAL,
            source=self._CONFIRMATION_RESPONSE_SOURCE,
            source_id=confirm_id,
            tags=["confirmation_response"],
            metadata={
                "confirm_id": confirm_id,
                "action": action,
                "reason": reason,
            },
        )
        return {"recorded": True, "confirm_id": confirm_id, "action": action}

    def check_confirmation_response(self, confirm_id: str) -> dict[str, Any] | None:
        """P1-4: 轮询检查确认请求是否已有用户响应。

        Returns
        -------
        dict | None
            有响应则返回 ``{"action": "approve"|"reject", "reason": str}``；无则 None
        """
        items = self._load_items()
        for item in items:
            if item.get("source") != self._CONFIRMATION_RESPONSE_SOURCE:
                continue
            metadata = item.get("metadata") or {}
            if metadata.get("confirm_id") != confirm_id:
                continue
            return {
                "action": str(metadata.get("action", "")),
                "reason": str(metadata.get("reason", "")),
                "response_item_id": item.get("item_id", ""),
            }
        return None

    # ── 建议投递（低打扰） ─────────────────────────────────────

    SUGGESTION_SURFACE_WORKBENCH = "workbench_card"
    SUGGESTION_SURFACE_FLOATING = "floating_entry"
    SUGGESTION_SURFACE_INBOX = "inbox_card"
    ALL_SUGGESTION_SURFACES = frozenset({
        SUGGESTION_SURFACE_WORKBENCH, SUGGESTION_SURFACE_FLOATING, SUGGESTION_SURFACE_INBOX,
    })

    def deliver_suggestion(
        self,
        *,
        text: str,
        action: str = "",
        reason: str = "",
        convertible_actions: list[dict[str, str]] | None = None,
        surface: str = "inbox_card",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """以低打扰方式投递建议到指定表面。

        Parameters
        ----------
        text : str
            建议显示文本
        action : str
            建议关联的动作键
        reason : str
            建议原因（为什么现在显示）
        convertible_actions : list[dict] | None
            可转换的动作列表
        surface : str
            目标表面：workbench_card / floating_entry / inbox_card
        metadata : dict | None
            附加元数据

        Returns
        -------
        dict
            交付结果，包含 delivered_to 和 item
        """
        resolved_surface = surface if surface in self.ALL_SUGGESTION_SURFACES else self.SUGGESTION_SURFACE_INBOX
        channel_map = {
            self.SUGGESTION_SURFACE_WORKBENCH: CHANNEL_INBOX,
            self.SUGGESTION_SURFACE_FLOATING: CHANNEL_FLOATING,
            self.SUGGESTION_SURFACE_INBOX: CHANNEL_INBOX,
        }
        channel = channel_map.get(resolved_surface, CHANNEL_INBOX)

        item = self.deliver(
            title=text,
            content=f"{reason}\n\n{text}" if reason else text,
            channel=channel,
            priority=PRIORITY_LOW,
            source=SOURCE_PROACTIVE,
            tags=["suggestion", action] if action else ["suggestion"],
            metadata={
                "suggestion_action": action,
                "suggestion_reason": reason,
                "suggestion_surface": resolved_surface,
                "convertible_actions": convertible_actions or [],
                **(metadata or {}),
            },
        )
        return {"delivered_to": resolved_surface, "item": item}

    # ── 通知回调绑定（晚绑定，用于主窗口初始化完成后接入托盘） ──

    def set_notification_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        """晚绑定桌面通知回调。主窗口初始化完成后调用，将通知路由到系统托盘。"""
        self._notification_callback = callback

    # ── 内部 ─────────────────────────────────────────────

    def _resolve_channel(self, priority: str, source: str) -> str:
        """根据优先级和来源自动决定通道。"""
        if priority in (PRIORITY_URGENT, PRIORITY_HIGH):
            return CHANNEL_BOTH
        if source == SOURCE_ANOMALY:
            return CHANNEL_BOTH
        if self._user_profile_service is not None:
            preferred = self._user_profile_service.get_preferred_delivery()
            if preferred in ALL_CHANNELS:
                return preferred
        return CHANNEL_INBOX

    def _persist_item(self, item: InboxItem) -> None:
        self.data_store.update_json(
            _STORAGE_KEY,
            [],
            lambda items: (list(items) if isinstance(items, list) else [])[-self._max_items:] + [item.to_dict()],
        )

    def _load_items(self) -> list[dict[str, Any]]:
        raw = self.data_store.load_json(_STORAGE_KEY, [])
        return list(raw) if isinstance(raw, list) else []

    def _update_item(self, item_id: str, patch: dict[str, Any]) -> bool:
        found = False

        def updater(items):
            nonlocal found
            result = []
            for item in (list(items) if isinstance(items, list) else []):
                if isinstance(item, dict) and item.get("item_id") == item_id:
                    item = {**item, **patch}
                    found = True
                result.append(item)
            return result

        self.data_store.update_json(_STORAGE_KEY, [], updater)
        return found

    def _send_notification(self, item: InboxItem) -> None:
        if self._notification_callback is not None:
            try:
                self._notification_callback(item.to_dict())
            except Exception as exc:
                logger.warning("桌面通知发送失败: %s", exc, exc_info=True)

    def _send_to_channel(self, callback, item: InboxItem, channel_name: str) -> None:
        if callback is not None:
            try:
                callback(item.to_dict())
            except Exception as exc:
                logger.warning("%s通道回调失败: %s", channel_name, exc, exc_info=True)
