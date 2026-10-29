"""Personal 个人知识体系 JSON-RPC handlers — 画像 + raw items（底座 L5 呈现）。

对应 docs/personal-knowledge-base-architecture.md：
- personal.profile.get      : 读取 UserProfileService 画像（四层）
- personal.profile.extract  : 扫描 raw items 提取画像（兴趣/联系人/焦点）并写回
- personal.profile.update   : 手动更新画像字段（focus_topics/current_focus/...）
- personal.raw_items.list   : 原始信息条目列表（按来源/敏感度过滤）
- personal.raw_items.stats  : raw items 统计
- personal.raw_items.set_sensitivity : 手动调整条目敏感度标记

隐私说明：敏感度标记已就位（personal/sensitive），本地 AI 部署完成后
sensitive 内容将仅由本地处理；当前不强制路由。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.handlers._async_rpc import POLLER_SCHEMA
from backend.handlers._async_rpc import poll_job_result as _poll_job_result
from backend.handlers._list_guard import clamp_limit

logger = logging.getLogger("siyi.backend.handlers.personal")


def register_personal(registry, context) -> None:
    """注册 personal.* JSON-RPC 方法。"""

    def _get_profile_service():
        return getattr(context, "user_profile_service", None)

    def _get_raw_item_repo():
        return getattr(context, "raw_item_repo", None)

    # ── personal.profile.get ──
    async def profile_get(**kwargs: Any) -> dict[str, Any]:
        svc = _get_profile_service()
        if svc is None:
            return {"success": False, "error": "user_profile_service unavailable"}
        try:
            profile = await asyncio.to_thread(svc.load)
            return {"success": True, "profile": profile}
        except Exception as exc:
            logger.exception("personal.profile.get 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("personal.profile.get", profile_get)

    # ── personal.profile.extract ──
    async def profile_extract(limit: int = 200, **kwargs: Any) -> dict[str, Any]:
        repo = _get_raw_item_repo()
        svc = _get_profile_service()
        if repo is None or svc is None:
            return {"success": False, "error": "raw_item_repo 或 user_profile_service 未初始化"}
        try:
            from services.personal_profile_extractor import PersonalProfileExtractor

            extractor = PersonalProfileExtractor(
                raw_item_repo=repo,
                user_profile_service=svc,
                ai_client=getattr(context, "ai_client", None),
            )
            result = await asyncio.to_thread(extractor.extract, max(1, min(int(limit), 500)))
            return {"success": True, **result}
        except Exception as exc:
            logger.exception("personal.profile.extract 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("personal.profile.extract", profile_extract)

    # ── personal.profile.update ──
    async def profile_update(
        focus_topics: list[str] | None = None,
        current_focus: str = "",
        active_project: str = "",
        common_sources: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        svc = _get_profile_service()
        if svc is None:
            return {"success": False, "error": "user_profile_service unavailable"}
        try:
            profile = await asyncio.to_thread(
                svc.save_settings_preferences,
                focus_topics=focus_topics,
                common_sources=common_sources,
                current_focus=current_focus or None,
                active_project=active_project or None,
            )
            return {"success": True, "profile": profile}
        except Exception as exc:
            logger.exception("personal.profile.update 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("personal.profile.update", profile_update)

    # ── personal.raw_items.list ──
    async def raw_items_list(
        source: str = "",
        sensitivity: str = "",
        limit: int = 100,
        **kwargs: Any,
    ) -> dict[str, Any]:
        repo = _get_raw_item_repo()
        if repo is None:
            return {"success": False, "error": "raw_item_repo 未初始化", "items": []}
        try:
            items = await asyncio.to_thread(
                repo.list,
                source=source or None,
                sensitivity=sensitivity or None,
                limit=clamp_limit(limit, default=100),
            )
            return {"success": True, "items": items, "count": len(items)}
        except Exception as exc:
            logger.exception("personal.raw_items.list 失败: %s", exc)
            return {"success": False, "error": str(exc), "items": []}

    registry.register("personal.raw_items.list", raw_items_list)

    # ── personal.raw_items.stats ──
    async def raw_items_stats(**kwargs: Any) -> dict[str, Any]:
        repo = _get_raw_item_repo()
        if repo is None:
            return {"success": False, "error": "raw_item_repo 未初始化"}
        try:
            stats = await asyncio.to_thread(repo.stats)
            return {"success": True, "stats": stats}
        except Exception as exc:
            logger.exception("personal.raw_items.stats 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("personal.raw_items.stats", raw_items_stats)

    # ── personal.raw_items.set_sensitivity ──
    async def raw_items_set_sensitivity(
        item_id: str = "",
        sensitivity: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        repo = _get_raw_item_repo()
        if repo is None:
            return {"success": False, "error": "raw_item_repo 未初始化"}
        if not item_id or sensitivity not in ("personal", "sensitive"):
            return {"success": False, "error": "item_id 与 sensitivity(personal|sensitive) 必填"}
        try:
            ok = await asyncio.to_thread(repo.set_sensitivity, item_id, sensitivity)
            return {"success": ok, "updated": ok}
        except Exception as exc:
            logger.exception("personal.raw_items.set_sensitivity 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("personal.raw_items.set_sensitivity", raw_items_set_sensitivity)

    # ── personal.review（个人回顾：周/月聚合 + AI 总结） ──
    async def review(period: str = "week", days: int = 0, **kwargs: Any) -> dict[str, Any]:
        """生成个人回顾：聚合近 N 天 raw items + AI 总结（同步返回终局结果）。

        Args:
            period: week(7天) / month(30天)
            days: 显式指定天数（优先于 period）

        S3-2 回退（2026-09-16 架构复审，audit §1）：入队信封版秒回 job_id，
        但前端 PersonalModule.tsx:105 直接把返回值 setReview 渲染回顾内容 →
        UI 渲染的是信封字段。消费面未适配 → 回退同步直调 run_personal_review。
        异步骨架（worker/.status/completed 事件）保留待 S3-2b。
        """
        return await run_personal_review(context, period=period, days=days)

    async def review_status(
        job_id: str = "",
        task_id: str = "",
        wait_seconds: float = 0.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """查询 personal.review 异步任务的状态与结果。"""
        return await _poll_job_result(
            context,
            job_id=job_id,
            task_id=task_id,
            capability_id="personal.review",
            wait_seconds=wait_seconds,
        )

    registry.register("personal.review", review)
    registry.register_with_schema("personal.review.status", review_status, schema=POLLER_SCHEMA)


async def run_personal_review(context: Any, *, period: str = "week", days: int = 0) -> dict[str, Any]:
    """个人回顾真实现 —— RPC 降级路径与 cron worker 共用（S3-2）。

    聚合近 N 天 raw items + AI 总结。AI 失败降级为纯统计（不报错）。
    """
    repo = getattr(context, "raw_item_repo", None)
    if repo is None:
        return {"success": False, "error": "raw_item_repo 未初始化"}
    from datetime import datetime, timedelta, timezone

    try:
        ndays = int(days) or (7 if period == "week" else 30)
        ndays = max(1, min(ndays, 90))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=ndays)).isoformat()
        items = await asyncio.to_thread(repo.list, limit=500, created_after=cutoff)
    except Exception as exc:
        logger.exception("personal.review 查询失败: %s", exc)
        return {"success": False, "error": str(exc)}

    # 聚合统计
    from collections import Counter

    by_source: Counter = Counter()
    by_sens: Counter = Counter()
    texts: list[str] = []
    contacts: list[str] = []
    for it in items:
        by_source[str(it.get("source") or "unknown")] += 1
        by_sens[str(it.get("sensitivity") or "personal")] += 1
        texts.append(str(it.get("content") or ""))
        sender = str((it.get("meta") or {}).get("sender") or "").strip()
        if sender and sender not in ("我", "自己", "系统") and sender not in contacts:
            contacts.append(sender)

    summary = ""
    if texts:
        try:
            ai = getattr(context, "ai_client", None)
            if ai is not None:
                excerpt = "\n".join(texts)[:3000]
                summary = await asyncio.to_thread(_generate_review_summary, ai, excerpt, ndays)
        except Exception as exc:
            logger.warning("personal.review AI 总结失败（降级纯统计）: %s", exc)

    return {
        "success": True,
        "period": period,
        "days": ndays,
        "stats": {
            "total": len(items),
            "by_source": dict(by_source),
            "by_sensitivity": dict(by_sens),
            "contacts": contacts[:20],
        },
        "summary": summary,
        "highlights": [str(t)[:120] for t in texts[:5]],
    }


def _generate_review_summary(ai_client: Any, excerpt: str, days: int) -> str:
    """AI 生成个人回顾总结（走现有 client 降级链）。"""
    response = ai_client.chat_completion(
        messages=[
            {
                "role": "system",
                "content": (f"你是个人回顾助手。用 3-5 句中文总结用户最近 {days} 天关注的话题、常见的生活动态与变化。语气平实，不编造内容。"),
            },
            {"role": "user", "content": excerpt},
        ],
        temperature=0.4,
        max_tokens=500,
    )
    raw = response
    if isinstance(raw, dict):
        raw = raw.get("content", raw.get("text", ""))
    return str(raw or "").strip()[:800]
