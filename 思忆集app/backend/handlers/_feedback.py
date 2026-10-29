# -*- coding: utf-8 -*-
"""P2.8 用户反馈学习闭环 JSON-RPC handlers。

注册 4 个 RPC 方法：
- ``chat.feedback.set`` — 写入 👍/👎 + 可选 edited_text/comment
- ``chat.feedback.list`` — 查询反馈列表（按 session_id 或 rating 过滤）
- ``chat.feedback.stats`` — 聚合统计（按天、按 rating）
- ``chat.feedback.delete`` — 删除单条反馈

另注册 3 个偏好聚合方法：
- ``chat.feedback.preference.get`` — 读取已聚合的偏好摘要文本
- ``chat.feedback.preference.set`` — 手动覆盖偏好摘要
- ``chat.feedback.aggregate`` — 触发一次偏好聚合（force=False 仅当达到阈值时执行）

所有同步 I/O 通过 ``asyncio.to_thread`` 包裹，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Tuple

from services.feedback_service import FeedbackService

logger = logging.getLogger("siyi.backend.handlers.feedback")

KEY_CHAT_FEEDBACK_ENABLED = "chat_feedback_enabled"
KEY_CHAT_FEEDBACK_AGGREGATE_THRESHOLD = "chat_feedback_aggregate_threshold"


def _get_service(context: "Any") -> "FeedbackService | None":
    """优先用 context 上挂载的 FeedbackService，否则懒构建一个。"""
    svc = getattr(context, "feedback_service", None)
    if svc is not None:
        return svc
    data_store = getattr(context, "local_data_store", None)
    if data_store is None:
        return None
    try:
        from services.feedback_service import FeedbackService

        # 修 keyword-only 误调（同族）：FeedbackService.__init__ 全参数
        # keyword-only（services/feedback_service.py:42），位置传参必抛
        # TypeError。生产路径由 headless_main 注入，这里兜底也须走 kwarg。
        svc = FeedbackService(
            data_store=data_store,
            settings_repository=getattr(context, "settings_repository", None),
            ai_client=getattr(context, "ai_client", None),
            event_bus=getattr(context, "event_bus", None),
        )
        return svc
    except Exception as exc:
        logger.warning("build FeedbackService failed: %s", exc)
        return None


def register_feedback(registry, context) -> None:
    """注册 chat.feedback.* JSON-RPC 方法。"""

    async def chat_feedback_set(
        session_id: "Any" = None,
        message_index: "Any" = None,
        rating: str = "",
        original_user_msg: str = "",
        original_ai_msg: str = "",
        edited_text: str = "",
        comment: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """记录一条用户反馈。

        参数：
        - ``session_id``: 会话 ID（可空）
        - ``message_index``: 反馈针对的消息序号（可空）
        - ``rating``: "up" 或 "down"（必填）
        - ``original_user_msg``: 用户原问题（可选，用于偏好学习）
        - ``original_ai_msg``: AI 原回复（可选，用于偏好学习）
        - ``edited_text``: 用户编辑后的版本（可选）
        - ``comment``: 文本评论（可选）
        """
        rating_clean = (rating or "").strip().lower()
        if rating_clean not in ("up", "down"):
            return {
                "recorded": False,
                "error": "rating must be 'up' or 'down'",
            }
        svc = _get_service(context)
        if svc is None:
            return {"recorded": False, "error": "feedback service unavailable"}
        mi = None
        if message_index is not None:
            try:
                mi = int(message_index)
            except (TypeError, ValueError):
                mi = None
        try:
            # 修 keyword-only 误调（2026-09-16 批3，与 task_queue 同族）：
            # FeedbackService.record_feedback 全参数 keyword-only（services/
            # feedback_service.py:64，rating 为必填 kwarg），位置传参必抛
            # TypeError 被 except 吞成 {"recorded": False}——前端点赞/点踩
            # 全部静默丢失，反馈数据从未落库。
            result = await asyncio.to_thread(
                svc.record_feedback,
                session_id=session_id,
                message_index=mi,
                rating=rating_clean,
                original_user_msg=original_user_msg,
                original_ai_msg=original_ai_msg,
                edited_text=edited_text,
                comment=comment,
            )
            return result
        except ValueError as exc:
            return {"recorded": False, "error": str(exc)}
        except Exception as exc:
            logger.warning("chat.feedback.set failed: %s", exc)
            return {"recorded": False, "error": str(exc)}

    async def chat_feedback_list(
        session_id: "Any" = None,
        rating: str = "",
        limit: int = 50,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """查询反馈列表，按时间倒序。

        参数：
        - ``session_id``: 按会话过滤（可空）
        - ``rating``: 按评分过滤 "up"/"down"（可空）
        - ``limit``: 返回上限，默认 50，最大 500
        """
        svc = _get_service(context)
        if svc is None:
            return {"items": [], "error": "feedback service unavailable"}
        rating_clean = (rating or "").strip().lower() or None
        if rating_clean is not None and rating_clean not in ("up", "down"):
            return {
                "items": [],
                "count": 0,
                "error": "rating must be 'up' or 'down'",
            }
        try:
            # 修 keyword-only 误调（2026-09-16 批3，与 task_queue 同族）：
            # FeedbackService.list_feedback 是 keyword-only（session_id/rating/limit
            # 在 * 之后），位置传参必抛 TypeError 被 except 吞成 items=[]——
            # 前端 feedback.list 永远拿到空列表（实测 stats 全 0 同症状）。
            items = await asyncio.to_thread(
                svc.list_feedback,
                session_id=session_id,
                rating=rating_clean,
                limit=limit,
            )
            return {"items": items, "count": len(items)}
        except Exception as exc:
            logger.warning("chat.feedback.list failed: %s", exc)
            return {"items": [], "error": str(exc)}

    async def chat_feedback_stats(
        days: int = 30,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """聚合统计：按 rating 计数 + 近 N 天每日分布。"""
        svc = _get_service(context)
        if svc is None:
            return {"error": "feedback service unavailable"}
        try:
            stats = await asyncio.to_thread(svc.get_stats, days)
        except Exception as exc:
            logger.warning("chat.feedback.stats failed: %s", exc)
            return {"error": str(exc)}

        settings_repo = getattr(context, "settings_repository", None)
        enabled = True
        threshold = 5
        if settings_repo is not None:

            def _read_cfg() -> "Tuple[bool, int]":
                _enabled = True
                _threshold = 5
                try:
                    _enabled = bool(settings_repo.get_setting(KEY_CHAT_FEEDBACK_ENABLED, True))
                except Exception:
                    logger.exception("读取聊天反馈启用开关配置失败")
                    pass
                try:
                    _threshold = int(settings_repo.get_setting(KEY_CHAT_FEEDBACK_AGGREGATE_THRESHOLD, 5))
                except Exception:
                    logger.exception("读取聊天反馈聚合阈值配置失败")
                    pass
                return _enabled, _threshold

            enabled, threshold = await asyncio.to_thread(_read_cfg)

        stats["enabled"] = enabled
        stats["aggregate_threshold"] = threshold
        stats["learning_samples"] = svc.count_learning_samples()
        return stats

    async def chat_feedback_delete(
        feedback_id: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """按 ID 删除单条反馈。"""
        if not feedback_id:
            return {"deleted": False, "error": "feedback_id is required"}
        svc = _get_service(context)
        if svc is None:
            return {"deleted": False, "error": "feedback service unavailable"}
        try:
            ok = await asyncio.to_thread(svc.delete_feedback, feedback_id)
            return {"deleted": ok}
        except Exception as exc:
            logger.warning("chat.feedback.delete failed: %s", exc)
            return {"deleted": False, "error": str(exc)}

    async def chat_feedback_preference_get(**kwargs: Any) -> "dict[str, Any]":
        """读取已聚合的偏好摘要文本。"""
        svc = _get_service(context)
        if svc is None:
            return {"summary": "", "error": "feedback service unavailable"}
        try:
            summary = await asyncio.to_thread(svc.get_preference)
            return {"summary": summary, "length": len(summary)}
        except Exception as exc:
            logger.warning("chat.feedback.preference.get failed: %s", exc)
            return {"summary": "", "error": str(exc)}

    async def chat_feedback_preference_set(
        summary: str = "",
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """手动覆盖偏好摘要文本。"""
        svc = _get_service(context)
        if svc is None:
            return {"ok": False, "error": "feedback service unavailable"}
        try:
            await asyncio.to_thread(svc.set_preference, summary or "")
            return {"ok": True, "length": len((summary or "").strip())}
        except Exception as exc:
            logger.warning("chat.feedback.preference.set failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    async def chat_feedback_aggregate(
        force: bool = False,
        **kwargs: Any,
    ) -> "dict[str, Any]":
        """触发一次偏好聚合。

        - ``force=False``（默认）：仅当 learning_samples >= threshold 时执行
        - ``force=True``：强制重新聚合（即使样本不足也用规则兜底）
        """
        svc = _get_service(context)
        if svc is None:
            return {"aggregated": False, "error": "feedback service unavailable"}
        try:
            # aggregate 是 async 协程：直接 await；其内部同步 I/O 已自行处理
            result = await svc.aggregate(force=bool(force))
            return result
        except Exception as exc:
            logger.warning("chat.feedback.aggregate failed: %s", exc)
            return {"aggregated": False, "error": str(exc)}

    registry.register("chat.feedback.set", chat_feedback_set)
    registry.register("chat.feedback.list", chat_feedback_list)
    registry.register("chat.feedback.stats", chat_feedback_stats)
    registry.register("chat.feedback.delete", chat_feedback_delete)
    registry.register("chat.feedback.preference.get", chat_feedback_preference_get)
    registry.register("chat.feedback.preference.set", chat_feedback_preference_set)
    registry.register("chat.feedback.aggregate", chat_feedback_aggregate)
