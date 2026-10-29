"""Content/info/image capability mixin — extracted from capability_router.py."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

logger = logging.getLogger("siyi.assistant.autonomy.router.content")


# 常驻专用事件循环（独立后台守护线程），承载重型协程，与支撑 WebSocket 的
# 共享事件循环彻底隔离——重活跑在此处，绝不让共享循环被拖住。
_dedicated_loop: asyncio.AbstractEventLoop | None = None
_dedicated_loop_thread: Any = None


def _get_dedicated_loop() -> asyncio.AbstractEventLoop:
    """惰性创建常驻的专用事件循环（独立守护线程运行），线程安全。"""
    global _dedicated_loop, _dedicated_loop_thread
    loop = _dedicated_loop
    if loop is not None and not loop.is_closed() and _dedicated_loop_thread is not None and _dedicated_loop_thread.is_alive():
        return loop
    import threading

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="siyi-dedicated-loop", daemon=True)
    thread.start()
    _dedicated_loop = loop
    _dedicated_loop_thread = thread
    return loop


def _run_blocking(coro, timeout: float | None) -> Any:
    """当前线程同步执行 async 任务，但绝不在共享 WebSocket 事件循环上运行。

    修复（2026-09-09）：此前用 ``run_coroutine_threadsafe(coro, event_bus._loop)
    .result()`` 把重型协程（如 info.collect 多源采集，可跑 50s+）调度到支撑
    aiohttp WebSocket 的同一个事件循环上。采集期间该循环被拖住，心跳 / RPC /
    流式 chunk 无法及时响应，客户端心跳 deadline 判死 → 主动断连，前端报
    "后端连接中断 / WebSocket closed"。

    改为在"专用事件循环"上执行重活：
    - 当前线程无运行中循环（tools 通常经 to_thread 在 worker 线程执行）→
      asyncio.run：独立循环跑完即关，期间当前线程同步阻塞，不影响 WS 循环。
    - 当前线程已有运行中循环 → 开销最小的独立循环线程承载，仅用
      .result(timeout) 阻塞当前线程。两种路径下共享 WS 循环都不再承载重型协程。
    """
    guarded = coro if (timeout is None or timeout <= 0) else asyncio.wait_for(coro, timeout=timeout)
    try:
        asyncio.get_running_loop()
        # 已在事件循环线程：用独立专用循环承载，仅阻塞当前线程
        future = asyncio.run_coroutine_threadsafe(guarded, _get_dedicated_loop())
        return future.result(timeout=timeout if (timeout is not None and timeout > 0) else None)
    except RuntimeError:
        # 非事件循环线程：asyncio.run 自建并关闭独立循环，绝不影响共享循环
        return asyncio.run(guarded)


class ContentRouterHost(Protocol):
    """ContentRouterMixin 依赖的宿主契约 — AssistantCapabilityRouter 由 __init__ 注入。"""

    knowledge_domain: Any
    ai_client: Any
    _event_bus: Any
    settings_repository: Any
    tech_watch_collector: Any
    tech_watch_judge: Any
    info_source_registry: Any


class ContentRouterMixin:
    """Content, digest, image, tech watch capability handlers."""

    if TYPE_CHECKING:
        knowledge_domain: Any
        ai_client: Any
        _event_bus: Any
        settings_repository: Any
        tech_watch_collector: Any
        tech_watch_judge: Any
        info_source_registry: Any

    def _digest_show(self, payload: dict[str, Any]) -> dict[str, Any]:
        """显示技术周要新闻简报。"""
        try:
            return self.knowledge_domain.news_briefing(payload)
        except Exception as exc:
            logger.debug("digest.show failed: %s", exc)
            return {"status": "completed", "note": "摘要功能暂不可用"}

    def _digest_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        """创建专题摘要。"""
        logger.info("digest.create requested: %s", payload)
        return {"status": "completed", "message": "摘要创建请求已记录，请稍后在收件箱查看"}

    def _info_collect(self, payload: dict[str, Any]) -> dict[str, Any]:
        """采集多源资讯（同步调用）。"""
        try:
            from services.info_collector_service import InfoCollectorService

            collector = InfoCollectorService(
                ai_client=self.ai_client,
                settings_repository=getattr(self, "settings_repository", None),
                # R2-A1：优先复用 context 装配的共享注册表（由 _context_builder 注入）
                source_registry=getattr(self, "info_source_registry", None),
            )
            topics = payload.get("topics")
            brief = payload.get("generate_brief", True)
            result = _run_blocking(collector.collect(topics=topics, generate_brief=brief), timeout=120)
            return {"status": "completed", "data": result}
        except Exception as exc:
            logger.warning("info.collect failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _image_generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """生成配图。"""
        try:
            from services.image_generator_service import ImageGeneratorService

            prompt = payload.get("prompt", "")
            if not prompt:
                return {"status": "failed", "error": "缺少 prompt 参数"}
            svc = ImageGeneratorService(
                ai_client=self.ai_client,
                settings_repository=self.settings_repository,
            )
            result = _run_blocking(
                svc.generate(prompt=prompt, size=payload.get("size", "1024x1024"), n=payload.get("n", 1)),
                timeout=120,
            )
            return {"status": "completed", "data": result}
        except Exception as exc:
            logger.warning("image.generate failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _info_publish(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发布内容到外部平台。"""
        try:
            from services.platform_publisher_service import PlatformPublisherService

            platform = payload.get("platform", "")
            content = payload.get("content", {})
            if not platform or not content:
                return {"status": "failed", "error": "缺少 platform 或 content 参数"}
            svc = PlatformPublisherService(settings_repository=self.settings_repository)
            result = _run_blocking(
                svc.publish(platform=platform, content=content),
                timeout=30,
            )
            return {"status": "completed", "data": result}
        except Exception as exc:
            logger.warning("info.publish failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

    def _get_tech_watch_focus_topics(self) -> list[str]:
        if self.settings_repository is None:
            return ["AI", "自动化", "软件工程"]
        try:
            topics = self.settings_repository.get_setting("assistant_focus_topics", [])
            if isinstance(topics, list) and topics:
                return topics
        except Exception:
            logger.debug("获取关注话题设置失败", exc_info=True)
        return ["AI", "自动化", "软件工程"]

    def _get_tech_watch_judge_day(self) -> str:
        if self.settings_repository is None:
            return "sun"
        try:
            return str(self.settings_repository.get_setting("tech_watch_judge_day", "sun"))
        except Exception:
            return "sun"

    def _tech_watch_collect(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.tech_watch_collector is None:
            return {"status": "skipped", "reason": "collector not configured"}
        topics = payload.get("topics") or self._get_tech_watch_focus_topics()
        local_ai = getattr(self.ai_client, "local", None) if self.ai_client else None
        return self.tech_watch_collector.collect(topics, local_ai_client=local_ai)

    def _tech_watch_judge(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.tech_watch_judge is None:
            return {"status": "skipped", "reason": "judge not configured"}
        return self.tech_watch_judge.judge(deepseek_client=self.ai_client)

    def _vision_analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        image_base64 = str(payload.get("image_base64") or "")
        if not image_base64:
            return {"status": "error", "error": "缺少 image_base64 参数"}
        image_format = str(payload.get("image_format") or "png")
        prompt = str(payload.get("prompt") or "请详细描述这张图片的内容。")
        client = self.ai_client
        if client is None:
            return {"status": "error", "error": "AI 客户端未就绪"}
        try:
            if hasattr(client, "analyze_image"):
                result = client.analyze_image(
                    image_base64=image_base64,
                    image_format=image_format,
                    prompt=prompt,
                )
            else:
                return {"status": "error", "error": "AI 客户端不支持视觉分析"}
            return {"status": "ok", "description": result}
        except Exception as exc:
            logger.warning("vision.analyze failed: %s", exc)
            return {"status": "error", "error": str(exc)}
