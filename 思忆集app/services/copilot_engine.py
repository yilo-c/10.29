"""CopilotEngine — AI 请求→路由→响应→EventBus 通知的统一 facade 引擎。

无 UI 依赖，可被任何 UI 组件消费。通过 ServiceRegistry 注册为单例服务。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from core.events import AppEvent, EventBus, EventHandler

logger = logging.getLogger("siyi.copilot_engine")


class CopilotEngine:
    """封装 AI 请求→路由→响应→EventBus 通知的完整链路。

    构造函数接收:
        event_bus: EventBus 实例（全局单例 core.events.event_bus）
        ai_client: AI 客户端（JudgeAwareClient / FallbackOpenAIClient）
        capability_router: 意图路由器（AssistantIntentRouter）
        settings_repo: 设置仓储
    """

    def __init__(
        self,
        event_bus: EventBus,
        ai_client: Any,
        capability_router: Any,
        settings_repo: Any,
    ) -> None:
        self._event_bus = event_bus
        self._ai_client = ai_client
        self._capability_router = capability_router
        self._settings_repo = settings_repo
        self._handlers: list[tuple[str, EventHandler]] = []
        self._registered = False

    # ── 核心 API ───────────────────────────────────────────

    def process_user_input(self, text: str) -> dict[str, Any]:
        """处理用户输入: AI 调用 → 意图分类 → 结构化响应。

        Returns:
            dict with keys: response_text, intent, confidence, source
        """
        if not text or not text.strip():
            return {"response_text": "", "intent": "empty", "confidence": 0.0, "source": "copilot_engine"}

        trimmed = text.strip()
        result: dict[str, Any] = {
            "response_text": "",
            "intent": "chat",
            "confidence": 0.0,
            "source": "copilot_engine",
        }

        # 1) 意图分类
        try:
            if hasattr(self._capability_router, "classify"):
                classification = self._capability_router.classify(trimmed)
                result["intent"] = getattr(classification, "intent", "chat")
                result["confidence"] = getattr(classification, "confidence", 0.0)
        except Exception:
            logger.debug("Intent classification failed, falling back to chat", exc_info=True)

        # 2) AI 调用
        try:
            messages = [{"role": "user", "content": trimmed}]
            response = self._ai_client.complete_text(messages)
            result["response_text"] = response or ""
        except Exception:
            logger.exception("AI call failed in process_user_input")

        # 3) 通过 EventBus 通知响应完成
        try:
            self._event_bus.emit(AppEvent(
                event_type="copilot.response",
                source="copilot_engine",
                payload={
                    "input": trimmed,
                    "intent": result["intent"],
                    "response": result["response_text"],
                },
            ))
        except Exception:
            logger.debug("Failed to emit copilot.response event", exc_info=True)

        return result

    def process_event(self, event: AppEvent) -> None:
        """根据事件类型自动调用对应的 handler。"""
        event_type = getattr(event, "event_type", "")

        if event_type == "inbox.delivered":
            self._on_inbox_delivered(event)
        elif event_type == "input.captured":
            self._on_input_captured(event)

    def register_event_handlers(self) -> None:
        """订阅 EventBus 中需要自动处理的事件。幂等。"""
        if self._registered:
            return
        self._registered = True

        # 收件箱投递
        self._event_bus.on("inbox.delivered", self._on_inbox_delivered)
        self._handlers.append(("inbox.delivered", self._on_inbox_delivered))

        # 输入捕获
        self._event_bus.on("input.captured", self._on_input_captured)
        self._handlers.append(("input.captured", self._on_input_captured))

        # 能力执行跟踪
        self._event_bus.on("capability.executed", self._on_capability_executed)
        self._handlers.append(("capability.executed", self._on_capability_executed))

        # MCP 生命周期事件
        self._event_bus.on("mcp.lifecycle.*", self._on_mcp_lifecycle)
        self._handlers.append(("mcp.lifecycle.*", self._on_mcp_lifecycle))

        # 搜索完成事件
        self._event_bus.on("search.completed", self._on_search_completed)
        self._handlers.append(("search.completed", self._on_search_completed))

        # 系统事件
        self._event_bus.on("system_event", self._on_system_event)
        self._handlers.append(("system_event", self._on_system_event))

        # 浏览器桥接事件（通过 EventBus 广播的）
        self._event_bus.on("browser.*", self._on_browser_event)
        self._handlers.append(("browser.*", self._on_browser_event))

        logger.debug("CopilotEngine event handlers registered (8 patterns)")

    def shutdown(self) -> None:
        """清理所有 EventBus 订阅。"""
        for pattern, handler in self._handlers:
            try:
                self._event_bus.off(pattern, handler)
            except Exception:
                logger.debug("Failed to unregister handler for %s", pattern, exc_info=True)
        self._handlers.clear()
        self._registered = False
        logger.debug("CopilotEngine shut down")

    # ── 事件 handler ───────────────────────────────────────

    def _on_inbox_delivered(self, event: AppEvent) -> None:
        """收件箱投递时自动分析条目内容（在线程中执行避免阻塞 EventBus）。"""
        payload = getattr(event, "payload", {}) or {}
        title = str(payload.get("title", ""))
        content = str(payload.get("content", ""))
        combined = f"{title} {content}".strip()
        if not combined:
            return
        threading.Thread(target=self._on_inbox_delivered_worker, args=(combined,), daemon=True).start()

    def _on_inbox_delivered_worker(self, combined: str) -> None:
        try:
            self.process_user_input(combined)
        except Exception:
            logger.exception("CopilotEngine auto-analysis failed for inbox.delivered")

    def _on_input_captured(self, event: AppEvent) -> None:
        """用户输入捕获时自动处理（在线程中执行避免阻塞 EventBus）。"""
        payload = getattr(event, "payload", {}) or {}
        text = str(payload.get("text", "") or payload.get("input", ""))
        if not text.strip():
            return
        threading.Thread(target=self._on_input_captured_worker, args=(text.strip(),), daemon=True).start()

    def _on_input_captured_worker(self, text: str) -> None:
        try:
            self.process_user_input(text)
        except Exception:
            logger.exception("CopilotEngine auto-processing failed for input.captured")

    def _on_capability_executed(self, event: AppEvent) -> None:
        """能力执行完成后记录日志。"""
        payload = getattr(event, "payload", {}) or {}
        cap_name = payload.get("capability", payload.get("name", "unknown"))
        result = payload.get("result", payload.get("status", ""))
        logger.debug("Capability executed: %s -> %s", cap_name, result)

    def _on_mcp_lifecycle(self, event: AppEvent) -> None:
        """MCP 服务生命周期事件 — 记录状态变化。"""
        payload = getattr(event, "payload", {}) or {}
        state = payload.get("state", payload.get("status", "unknown"))
        service = payload.get("service", payload.get("name", "unknown"))
        logger.info("MCP lifecycle: %s = %s", service, state)

    def _on_search_completed(self, event: AppEvent) -> None:
        """搜索完成事件 — 记录搜索上下文以优化路由。"""
        payload = getattr(event, "payload", {}) or {}
        query = str(payload.get("query", ""))
        results = payload.get("results", payload.get("count", 0))
        if query:
            logger.debug("Search completed: q=%s hits=%s", query[:60], results)

    def _on_system_event(self, event: AppEvent) -> None:
        """系统级事件 — 启动、关闭、配置变更等。"""
        payload = getattr(event, "payload", {}) or {}
        sub_type = payload.get("sub_type", payload.get("type", "unknown"))
        logger.debug("System event: %s", sub_type)

    def _on_browser_event(self, event: AppEvent) -> None:
        """浏览器桥接事件 — 感知浏览器操作结果。"""
        payload = getattr(event, "payload", {}) or {}
        action = event.event_type.replace("browser.", "")
        url = str(payload.get("url", "") or "")
        summary = str(payload.get("summary", "") or "")
        logger.debug("Browser event: %s url=%s", action, url[:80] if url else "none")
        if summary:
            logger.debug("Browser summary: %s", summary[:120])
